# Task 01 — WhatsApp-style web UI for the demo

Branch: `muse/01-web-ui`. Allowed to create/edit: `app/channels/web.py`, `app/static/**`, `tests/test_web.py`,
`README.md` (one new paragraph under "Run it"). Do not touch the files listed in `AGENTS.md` rule 2.

## Goal

Judges must be able to run the agent in a browser with no WhatsApp setup. One page that looks like a WhatsApp
chat with "Çırak". Everything goes through `app.core.handle` — the same function the WhatsApp channel uses.

## Behaviour

- `python -m app.channels.web` starts a FastAPI app on `http://127.0.0.1:8000` (uvicorn), serving the page at `/`.
- Chat UI: messages from the merchant on the right (green bubbles), Çırak on the left (white), dark header with
  the name "Çırak" and the current demo business name. Mobile-width column (max ~480 px) centred on a neutral
  background. No frameworks; plain HTML/CSS/JS in `app/static/`.
- Composer: text input, a 📎 button to attach images (multiple, jpg/png), a 🎙️ button that records a voice note
  with `MediaRecorder` (webm/opus) and sends it on release, and a send button.
- Endpoints (JSON):
  - `POST /api/message` with multipart: `text` (optional), `audio` (optional file), `images[]` (optional files).
    Saves uploads under `settings.out_dir / "web" / <session>` and calls `core.handle` once per image (kind
    `image`, no caption) then once for the instruction (kind `text` or `audio`). Returns `{"replies":[{"text":…,
    "image_url":…}], "status": [...]}` where `status` are the strings passed to `notify` (e.g. "⏳ Bakıyorum…").
  - `GET /api/media/<path>` serves generated images (campaign visual) from `out_dir` only — reject `..`.
  - `POST /api/reset` → `core.handle(session, "text", "/sifirla")`.
- The approval card from `core` is plain text with `*bold*` WhatsApp markup; render `*…*` as bold and keep line
  breaks. Replies with `image_path` show the image inside the bubble with the text as caption.
- Session: one in-memory `core.Session` per browser, keyed by a random cookie `cirak_sid`. Process the requests of
  one session sequentially (a lock per session) so a double-click cannot approve twice.
- Quick actions under the composer: three buttons that pre-fill the input with the demo instructions from
  `README.md` (boutique, salon, and "/salon" switch). Keep them small.
- While waiting, show the `status` strings as grey centred system lines, like WhatsApp date separators.

## Acceptance

- `python -m pytest -q` passes; `tests/test_web.py` covers: session cookie set, `POST /api/message` with text goes
  through a monkeypatched `core.handle` and returns its replies, path traversal on `/api/media` → 400/404,
  a second request on the same session waits for the first.
- Manual: with a real `MODEL_API_KEY`, typing the boutique instruction shows "⏳ Bakıyorum…", then the approval
  card; typing `evet` shows the report with the campaign visual inline.
- No change to the WhatsApp channel behaviour (its tests untouched and green).
