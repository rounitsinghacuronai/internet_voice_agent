"""Regression tests for a real production bug (session 929a148d33af): the
caller explicitly said, twice, that they did not want to be transferred to a
senior executive while a transfer handoff was already in progress — and the
transfer happened anyway, ~19 seconds later.

Root cause: _handle_transfer() runs inside _run_turn's `async with
self._turn_lock:` section, which can take many seconds (spoken "connecting
you" line + its full playback drain + the real Exotel API call). While that
task is still running, a NEW utterance from the caller completes its own
(unlocked) audio pipeline and gets assigned to `self._active_turn_task` by
_on_utterance() — well before it ever reaches `_turn_lock.acquire()`, where it
just blocks behind the still-running transfer. `_trigger_barge_in()` cancels
whatever `_active_turn_task` currently points to — the new, blocked arrival —
while the actual transfer keeps running completely untouched. Confirmed via
the log: "turn interrupted after STT (turn 24)" fires twice while "TRANSFER
initiated" only completes afterward.

Fix: track `_locked_task`, the task genuinely holding `_turn_lock` right now,
set on entry to _run_turn's locked section and cleared on exit (see
_run_turn/_run_turn_locked in ws_voice.py). `_trigger_barge_in()` now cancels
`_locked_task` in addition to `_active_turn_task`, so barge-in can reach and
stop the transfer that's actually in flight, not just whatever queued up
behind it — restoring the behaviour the existing "barge-in stays live" design
comments already claimed but didn't actually deliver.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import get_settings


def _session():
    """Build a VoiceSession with the heavy audio components stubbed out
    (mirrors the fixture in test_silence_watchdog.py)."""
    from backend.app.api import ws_voice

    deps = MagicMock()
    deps.settings = get_settings()
    deps.llm = MagicMock()
    deps.tools = MagicMock()

    with patch.object(ws_voice, "SileroVAD", MagicMock()), \
         patch.object(ws_voice, "Endpointer", MagicMock()), \
         patch.object(ws_voice, "AudioPipeline", MagicMock()):
        sess = ws_voice.VoiceSession(MagicMock(), deps)

    sess._speak_sentence = AsyncMock()
    sess._drain_playback = AsyncMock()
    sess._send = AsyncMock()
    sess.ws = MagicMock()
    sess.ws.close = AsyncMock()
    return sess


@pytest.mark.asyncio
async def test_barge_in_cancels_the_task_actually_holding_turn_lock():
    """The core fix: a barge-in must reach the REAL in-flight work (e.g. a
    transfer handoff), not just a newer task that's merely queued up behind
    it waiting on _turn_lock."""
    sess = _session()

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_locked_work():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    real_task = asyncio.create_task(slow_locked_work())
    sess._locked_task = real_task
    await asyncio.wait_for(started.wait(), timeout=1)

    # Simulate _on_utterance() assigning a LATER utterance's task to
    # _active_turn_task while it's still blocked acquiring _turn_lock —
    # it never touches the real work, so it must NOT be what gets cancelled.
    newcomer_task = asyncio.create_task(asyncio.sleep(10))
    sess._active_turn_task = newcomer_task

    await sess._trigger_barge_in()

    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert real_task.cancelled() or (real_task.done() and cancelled.is_set())

    newcomer_task.cancel()
    for t in (real_task, newcomer_task):
        try:
            await t
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_barge_in_still_cancels_when_locked_task_equals_active_task():
    """The common case (no second utterance queued up): _locked_task IS
    _active_turn_task. Cancelling both must not double-raise or error."""
    sess = _session()

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(work())
    sess._active_turn_task = task
    sess._locked_task = task
    await asyncio.wait_for(started.wait(), timeout=1)

    await sess._trigger_barge_in()   # must not raise

    await asyncio.wait_for(cancelled.wait(), timeout=1)
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_run_turn_sets_and_clears_locked_task_around_the_locked_section():
    """Integration check: _run_turn actually populates _locked_task for the
    duration of its work and clears it afterward, so _trigger_barge_in has
    something real to cancel while a turn (or a transfer inside one) runs."""
    sess = _session()

    saw_locked_task_during_run = {}

    async def fake_manager_run_turn(*args, **kwargs):
        # While this generator is mid-flight, _locked_task must be set to
        # the task currently executing _run_turn.
        saw_locked_task_during_run["value"] = sess._locked_task
        return
        yield  # pragma: no cover - makes this an async generator

    sess.manager.run_turn = fake_manager_run_turn
    sess.manager.end_call_requested = False
    sess.manager.transfer_requested = None
    sess.manager.memory = MagicMock()
    sess.manager.memory.snapshot = MagicMock(return_value={})

    assert sess._locked_task is None
    await sess._run_turn("नमस्ते", "hi")

    assert saw_locked_task_during_run["value"] is not None
    assert saw_locked_task_during_run["value"] is asyncio.current_task()
    # Cleared once the locked section exits.
    assert sess._locked_task is None
