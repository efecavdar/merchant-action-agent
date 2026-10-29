# Task 25 — Show the work live: step-by-step progress in WhatsApp and the web demo

Branch: `muse/25-live-progress`. Start after task 23 is merged (uses `app/i18n.py`). Allowed to edit: `app/executor.py`
(an optional progress callback only — no change to what or how it executes), `app/core.py` (pass the callback),
`app/i18n.py`, `app/channels/web.py` + `app/static/**` (render progress lines), tests.

## Why

After "evet" the owner waits in silence until the final report. Judges and merchants should *see* the apprentice work:
each step announced as it happens, from facts, with the KEDD panel lighting up at the same time (gateway K9 writes a
panel notification for every write).

## Change

1. `executor.execute(..., on_step=None)`: before the first action call `on_step("start", 0, n, None)`; after each
   action `on_step("ok" | "failed" | "skipped", i, n, step_record)`. The callback must never affect execution: wrap it,
   swallow and log (type only) any exception it raises.
2. `core._execute_pending` passes a callback that sends one short line per event through `notify`, rendered by
   `app/i18n.py` templates **from the step record's facts only** (never model text), in `session.lang`:
   - start: "▶️ Başlıyorum — 4 adım" / "▶️ Starting — 4 steps"
   - ok: "✅ 1/4 Ürün eklendi: İpek Fular — 850 ₺", "✅ 2/4 İndirim planlandı: %15, 1–2 Kas",
     "✅ 3/4 Kampanya: 23 kişiye gitti", "✅ 4/4 Instagram: yarın 10:00'da yayında"
   - failed: "⛔ 2/4 İndirim yapılamadı: <gateway message> — kalan adımları durdurdum"
   - In KEDD mode the final report gets one extra line: "🔔 Hepsi KEDD panelinde bildirimlerde."
   Keep it to one message per step (WhatsApp rate is fine for ≤ 12 steps); progress notes are best effort like the
   existing ones.
3. Web demo: progress lines appear as status lines **while** the request runs. If the current request/response model
   cannot stream, add `GET /api/progress?since=<n>` polled every 700 ms during a busy request (session-scoped, same
   rules as `/api/trace`: no PII, no model text).

## Acceptance

`python -m pytest -q` green with tests for: callback order and counts on success and on a mid-plan failure; a
callback that raises does not change the execution result; lines rendered in TR and EN from facts; no customer phone
numbers; web progress endpoint is session-scoped and returns only rendered lines.
