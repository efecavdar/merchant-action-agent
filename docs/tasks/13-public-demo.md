# Task 13 — Public demo URL for judges (mock only, rate-limited)

Branch: `muse/13-public-demo`. Allowed to edit: `modal_app.py` (add one function), `app/channels/web.py`,
`app/config.py` (two new settings), `app/static/**`, `tests/test_web.py`, `README.md` (a "Try it" line at the top).
Start after task 12 is merged (`git pull` first).

## Goal

A public HTTPS link where anyone can use the web UI against the **mock** businesses, without WhatsApp or any setup.

## Safety rules (all enforced in code, all tested)

1. Public mode = `PUBLIC_DEMO=true`. In public mode `/kedd` answers "Bu demo yalnız örnek işletmelerle çalışır." and
   never builds a `KeddBackend`, whatever the env contains. The Modal function for the public demo must NOT receive the
   KEDD secret or WhatsApp secrets: give it its own `modal.Secret.from_dict` built at deploy time from only
   `MODEL_API_KEY` (read from the local `.env` in `modal_app.py`) plus `PUBLIC_DEMO=true`, `BACKEND=mock`.
2. Rate limits (in memory is fine, one container): per client IP 20 messages per hour and 60 per day; global
   `PUBLIC_DEMO_DAILY_CAP` (default 400) model-calling requests per UTC day. Over the limit → a friendly Turkish +
   English message, no model call. Commands (`/butik`, `/salon`, `/sifirla`) and greetings don't count.
3. Uploads: images ≤ 5 MB each, max 4 per message, jpg/png only (check magic bytes, not just the extension); audio ≤ 2 MB.
   Reject others with a message.
4. Sessions expire after 30 minutes idle; at most 500 live sessions (evict oldest).
5. No user content is logged; log only counts and timings.

## Deployment

`@app.function(..., max_containers=1)` + `@modal.asgi_app()` named `demo`, same image, its own secret as above, no
volume for sessions (outputs may go to a temp dir; generated visuals served from there). `modal deploy` prints its URL;
put it in README as "Try it live: <url>" with one line saying it runs on synthetic data.

## UI

In public mode show a slim banner: "Demo: örnek işletme verisi · Synthetic demo data · Nothing is sent to real
customers". A language toggle is NOT needed.

## Acceptance

`python -m pytest -q` green, with tests for: `/kedd` refused in public mode even if KEDD env vars are set, per-IP and
global limits (no model call when limited), upload checks (magic bytes), session expiry/eviction, banner present in
public mode only.
