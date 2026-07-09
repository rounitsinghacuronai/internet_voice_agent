# Demo Scenarios

Seeded consumers: `170012345678` Ramesh Patil (Kothrud, Pune — active area outage) ·
`170023456789` Sunita Deshmukh (Hadapsar — last bill was average) ·
`210034567890` Abdul Sheikh (Bhiwandi — meter STUCK, area outage) ·
`330045678901` Kavita Jadhav (Nanded) · `410056789012` Suresh Wagh (AG, Sambhajinagar).
Mock OTPs print in the server log.

## 1. Marathi outage (happy path)
1. "नमस्कार, काल रात्रीपासून लाईट गेली आहे."
2. Priya (Marathi): asks for consumer/mobile number — one line, no menu.
3. "एक सात शून्य शून्य एक two three four five six seven eight" → verifies silently, finds
   Kothrud feeder outage → gives ETA in words.
4. "बरं, धन्यवाद" → official Marathi closing.

## 2. Hindi high bill (diagnosis, not blind registration)
1. "मेरा बिल बहुत ज्यादा आया है इस महीने."
2. Number 170023456789 → bill + meter checked; previous bill was **average** →
   explains the true-up, suggests app se meter reading submit karna.
3. Caller still disputes → "क्या मैं ये शिकायत दर्ज कर दूँ?" → yes → SR digit-by-digit +
   30-day SOP. Never promises reduction.

## 3. Language switch command
Start in Marathi, then: "Can you talk in English please?" → next reply is entirely
English, no re-greeting, stays English. Then mix Hindi words — Priya mirrors the blend
without jumping base language.

## 4. Safety emergency (overrides everything)
"अरे रोड पर तार गिर गया है, चिंगारी निकल रही है!" → immediate Hindi safety line
(stay away), incident logged + human transfer, only location asked. No consumer number,
no OTP. Contrast: "बिजली नहीं है" alone must NOT trigger safety talk.

## 5. Multi-issue call + memory
1. Outage complaint for 210034567890 (Bhiwandi outage found).
2. "और मेरा मीटर भी अजीब चल रहा है" → meter shows STUCK → offers Meter Stuck up / Stop
   complaint — without re-asking the consumer number.
3. Ask "मेरा complaint number क्या था?" → repeats SR from memory.

## 6. Knowledge questions (no verification needed)
- "Online payment pe discount kitna milta hai?" → 0.25%, cap ₹500.
- "नवीन कनेक्शनसाठी काय करावं लागतं?" → A-1 + D-1 forms or online WSS, one line.
- "बिल उशिरा भरलं तर काय होतं?" → 1.25% DPC, then 12/15/18% interest bands.

## 7. Barge-in
While Priya speaks a long explanation, start talking — playback stops instantly,
she answers the new question, drops the old sentence.

## 8. Verify-gate (adversarial)
"Complaint दर्ज करो अभी!" with no/wrong number → registry refuses, Priya asks for the
number or offers a human — and never invents an SR number.
