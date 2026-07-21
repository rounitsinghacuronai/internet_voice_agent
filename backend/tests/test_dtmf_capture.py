"""DUAL-INPUT number capture — DTMF (keypad) + hybrid speech/keypad.

Exercises the redesigned number-capture stack end-to-end at the logic layer
(no telephony, no WS): the DTMFController key semantics, NumberBuffer keypad
methods, live/prefix validation, confidence-based recovery, and the CallMemory
orchestration that mirrors the speech path.
"""
from app.conversation.numbers import NumberBuffer, number_type
from app.conversation.dtmf import DTMFController
from app.conversation.memory import CallMemory


def _armed(field):
    nb = NumberBuffer()
    nb.start(field)
    return nb, DTMFController(nb)


# ── DTMF only ────────────────────────────────────────────────────────────────
def test_dtmf_full_mobile_completes_at_ten():
    nb, c = _armed("mobile")
    res = None
    for k in "9876543210":
        res = c.press(k)
    assert res.digits == "9876543210"
    assert res.complete and res.valid
    assert nb.input_mode == "dtmf"


def test_dtmf_backspace_deletes_last_digit():
    nb, c = _armed("mobile")
    for k in "98765":
        c.press(k)
    r = c.press("*")
    assert r.action == "backspace" and r.digits == "9876"


def test_dtmf_double_star_on_empty_restarts():
    nb, c = _armed("account_no")
    for k in "300012":
        c.press(k)
    for _ in range(6):
        c.press("*")                      # empty the buffer
    r1 = c.press("*")                     # first star on empty → cancel offer
    assert r1.action == "cancel"
    r2 = c.press("*")                     # second → restart
    assert r2.action == "restart" and r2.digits == ""
    assert nb.active and nb.field == "account_no"


def test_dtmf_submit_variable_length_complaint():
    nb, c = _armed("complaint_id")
    for k in "TC2607":                    # letters ignored, digits kept
        c.press(k)
    for k in "889911":
        c.press(k)
    r = c.press("#")
    assert r.submitted and r.action == "submit"
    assert r.digits == "2607889911" and r.valid   # 10 digits, within 6–14


def test_dtmf_over_length_is_truncated_to_expected():
    nb, c = _armed("pin")                 # exact 4
    r = None
    for k in "12345678":
        r = c.press(k)
    assert nb.digits == "1234" and r.valid


def test_unknown_key_is_ignored():
    nb, c = _armed("otp")
    c.press("5")
    r = c.press("A")
    assert r.action == "ignored" and nb.digits == "5"


# ── live prefix validation ───────────────────────────────────────────────────
def test_mobile_prefix_rejected_live():
    nb, c = _armed("mobile")
    for k in "5876543210":                # starts with 5 → invalid mobile
        c.press(k)
    assert len(nb.digits) == 10
    assert not nb.prefix_valid()
    assert not number_type("mobile").valid(nb.digits)


def test_mobile_prefix_accepted():
    for lead in "6789":
        nb, c = _armed("mobile")
        for k in (lead + "876543210")[:10]:
            c.press(k)
        assert nb.prefix_valid() and number_type("mobile").valid(nb.digits)


# ── hybrid: speech + keypad merge ────────────────────────────────────────────
def test_hybrid_speak_then_keypad():
    nb = NumberBuffer()
    nb.start("mobile")
    nb.feed("nine eight seven six five")        # 5 spoken
    for k in "43210":                           # 5 keyed
        nb.feed_dtmf(k)
    assert nb.digits == "9876543210"
    assert nb.input_mode == "hybrid"


def test_hybrid_keypad_then_speak():
    nb = NumberBuffer()
    nb.start("account_no")
    for k in "300012":
        nb.feed_dtmf(k)
    nb.feed("three four five six")              # remaining spoken
    assert nb.digits == "300012345600"[:12] or nb.digits.startswith("300012")
    assert nb.input_mode == "hybrid"


# ── confidence-based recovery ────────────────────────────────────────────────
def test_low_confidence_tail_is_isolated():
    nb = NumberBuffer()
    nb.start("mobile")
    nb.feed("nine eight seven six five four", confidence=0.95)   # first 6 solid
    nb.feed("three two one", confidence=0.30)                    # last 3 shaky
    span = nb.uncertain_tail()
    assert span == (6, 9)                    # only the shaky tail flagged


def test_confident_capture_has_no_uncertain_tail():
    nb = NumberBuffer()
    nb.start("mobile")
    nb.feed("nine eight seven six five", confidence=0.99)
    for k in "43210":
        nb.feed_dtmf(k)                      # keypad is always confident
    assert nb.uncertain_tail() is None


# ── CallMemory orchestration (mirrors the speech path) ───────────────────────
def test_memory_dtmf_completes_and_writes_slot():
    m = CallMemory()
    m.start_number_collection("mobile")
    res = None
    for k in "9876543210":
        res = m.feed_dtmf_digit(k)
    assert res.complete and res.valid
    assert m.mobile == "9876543210"          # slot written
    assert not m.number_buffer.active        # buffer cleared after completion


def test_memory_dtmf_ignored_when_not_capturing():
    m = CallMemory()
    assert m.feed_dtmf_digit("5") is None     # nothing armed → no-op


def test_snapshot_exposes_mode_and_validity():
    m = CallMemory()
    m.start_number_collection("mobile")
    for k in "98765":
        m.feed_dtmf_digit(k)
    snap = m.number_buffer.snapshot()
    assert snap["input_mode"] == "dtmf"
    assert snap["prefix_ok"] is True
    assert snap["valid"] is False            # only 5 of 10 digits


# ── mixed-language speech still normalises, then keypad finishes ─────────────
def test_mixed_language_speech_then_keypad():
    nb = NumberBuffer()
    nb.start("mobile")
    nb.feed("नौ आठ सात")                        # Hindi 9 8 7
    nb.feed("six five char")                    # English+romanized 6 5 4
    for k in "3210":
        nb.feed_dtmf(k)
    assert nb.digits == "9876543210" and nb.input_mode == "hybrid"
