# Demo Scenarios

Seeded customers: `300012345678` Ramesh Patil (Kothrud, Pune — fiber 100 Mbps, mobile
tower outage in area) · `300023456789` Sunita Deshmukh (Hadapsar — postpaid 599, bill DUE) ·
`210034567890` Abdul Sheikh (Bhiwandi — fiber 300 Mbps, red LOS, area fiber cut) ·
`330045678901` Kavita Jadhav (Nanded — prepaid 299) · `410056789012` Suresh Wagh
(enterprise leased line, Sambhajinagar). Mock OTPs print in the server log.

## 1. Marathi broadband down (happy path)
1. "नमस्कार, काल रात्रीपासून नेट चालत नाही."
2. Agent (Marathi): asks for account/mobile number — one line, no menu.
3. "दोन एक शून्य शून्य three four five six seven eight nine zero" → verifies silently,
   finds the Bhiwandi fiber cut → gives restoration ETA in words. No engineer visit
   offered while an area outage explains the fault.
4. "बरं, धन्यवाद" → official Marathi closing.

## 2. Hindi high bill (diagnosis, not blind registration)
1. "मेरा बिल बहुत ज्यादा आया है इस महीने."
2. Number 300023456789 → bill checked; add-on data packs and GST explained line by
   line. Never promises reduction.
3. Caller still disputes → "क्या मैं ये शिकायत दर्ज कर दूँ?" → yes → ticket digit-by-digit
   + 72-hour billing-review SLA.

## 3. Language switch command
Start in Marathi, then: "Can you talk in English please?" → next reply is entirely
English, no re-greeting, stays English. Then mix Hindi words — the agent mirrors the
blend without jumping base language.

## 4. Fraud incident (overrides everything)
"किसी ने OTP माँग के मेरे अकाउंट से पैसे निकाल लिए!" → immediate Hindi protect-the-customer
line (never share OTP/passwords), priority incident logged + human transfer. No OTP
flow, no plan talk. Contrast: "नेट नहीं है" alone must NOT trigger fraud talk.
Variant: "मेरा फोन चोरी हो गया" → SIM blocked immediately after verification (no OTP —
the caller cannot receive one), replacement explained, CEIR advice for the handset.

## 5. Multi-issue call + memory
1. Broadband ticket for 210034567890 (Bhiwandi fiber cut found).
2. "और मेरा mobile pe bhi signal nahi aa raha" → area tower status checked → offers the
   matching mobile complaint — without re-asking the account number.
3. Ask "मेरा ticket number क्या था?" → repeats the TC number from memory.

## 6. Knowledge questions (no verification needed)
- "Recharge fail hua, paise kat gaye — refund kab tak?" → auto-reversal in 5–7 working days.
- "eSIM कसं activate करायचं?" → OTP + registered email + QR, one line.
- "Port karna hai number, kya process hai?" → SMS PORT to 1900, UPC, KYC, 3–5 days.

## 7. Structured troubleshooting (fiber)
"Internet slow hai" with a healthy line → diagnostics show Wi-Fi congestion → suggests
5GHz / router placement / wired test BEFORE any engineer visit. "Net band hai" with red
LOS → says plainly a reboot won't fix a fiber break → area outage check → engineer visit.

## 8. Barge-in
While the agent speaks a long explanation, start talking — playback stops instantly,
it answers the new question, drops the old sentence.

## 9. Verify-gate (adversarial)
"Complaint दर्ज करो अभी!" with no/wrong number → registry refuses, the agent asks for the
number or offers a human — and never invents a ticket number.

## 10. Remote fix on the call
"Router restart kar do" (verified, healthy line) → remote ONT reboot command sent, caller
told service returns in two–three minutes — no engineer, no store visit.
