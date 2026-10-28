# Task 12 — Web UI polish for the demo video

Branch: `muse/12-web-ui-polish`. Allowed to edit: `app/static/**`, `app/channels/web.py`, `tests/test_web.py`.

## Bugs seen live

1. While a request runs (planning takes 30–60 s), `send()` and `requestBrief()` silently return because of `busy`.
   The owner types a message, presses send, nothing happens and the text stays in the box.
2. No visible sign that Çırak is working; status lines ("⏳ Bakıyorum…") only appear after the response arrives.

## Change

- While busy: show a WhatsApp-style typing bubble ("Çırak yazıyor…" with three animated dots) on the left immediately
  when a request starts; remove it when the response arrives.
- While busy, keep the input enabled but turn the send button grey; pressing send/Enter **queues** the message (show it
  right away as a grey "pending" bubble on the right) and sends it automatically after the current request finishes.
  Max one queued message; a second attempt shows a small system line "Bir önceki isteğini bitiriyorum…".
- Show the user's own bubble immediately for the brief button too ("☀️ Sabah özeti").
- Quick buttons: replace the duplicate "/salon" button with "🔗 KEDD" (sends `/kedd`); keep Butik/Salon/Sabah özeti.
- Header shows the active business and a small badge: "demo" for butik/salon, "KEDD" for kedd (from the response's
  `business` field).
- Images in bubbles open full size in a lightbox on click (for the campaign visual in the video).
- Keep everything plain HTML/CSS/JS, no frameworks; mobile width unchanged.

## Acceptance

`python -m pytest -q` green; tests cover the `business` field in responses and that `/api/message` still serialises
per session. Manual: start a brief, type a message during it → it appears as pending and is sent after the brief.
