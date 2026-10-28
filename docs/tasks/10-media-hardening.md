# Task 10 — Harden the voice + photo pipeline before real media arrives

Branch: `muse/10-media-hardening`. Allowed to edit: `app/pipeline.py`, `app/meta_client.py`, tests,
`scripts/check_api.py`. Do not touch the files in AGENTS.md rule 2.

SAM 3.1 and Voice Transcribe were written from the docs and have not seen real WhatsApp media yet. Make every failure
mode degrade gracefully and visibly, so a bad photo or voice note never kills the conversation.

1. Voice: WhatsApp sends `audio/ogg; codecs=opus`. `to_wav_24k_mono` must handle ogg/opus, m4a, mp3, webm (web UI) and
   reject > 3 minutes with a clear `ValueError("Sesli not 3 dakikadan uzun…")`. Empty/whitespace transcript →
   `ValueError("Sesli notu anlayamadım, tekrar dener misin ya da yazar mısın?")`. ffmpeg missing → clear error.
2. SAM: if the stream errors or returns no parsable boxes, fall back to the whole image and add a note (today's
   behaviour for "no boxes" — extend it to exceptions). Drop boxes covering > 90 % of the image when several boxes
   exist (background false positives); cap at 8 crops per photo; de-duplicate boxes with IoU > 0.6. Unit-test
   `crop_boxes` with synthetic boxes.
3. `include_confidence` / `metadata` on the SAM call: if the API rejects the parameter (400), retry once without it.
   Same pattern for Muse Image `extra_body` fields (retry once with only `size`).
4. Vision JSON: `describe_crop` already falls back; also clamp `name` to 5 words and `category` to the allowed list.
5. `scripts/check_api.py --audio X --image Y` prints, per step, PASS/FAIL and the exception text, and continues to the
   next step instead of stopping.

Acceptance: `python -m pytest -q` green with offline tests for every branch above (monkeypatch the client/stream,
generate tiny audio with ffmpeg only if available — skip the test otherwise).
