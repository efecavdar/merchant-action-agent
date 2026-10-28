# Task 06 — Proactive morning brief ("Günaydın" from Çırak)

Branch: `muse/06-morning-brief`. Allowed to edit: `app/core.py` (add one function), `modal_app.py` (add one scheduled
function), new `app/brief.py`, `tests/test_brief.py`, `README.md` (one row in the "What the owner can ask" table).

## Goal

Every morning Çırak writes first, so the owner never has to open a dashboard:

> Günaydın ☀️ Bugün 9 randevun var, 14:00-16:00 arası boş. Ceren S. 5 saattir cevap bekliyor.
> Boyama yaptıran sadık müşterilere öğleden sonrası için %15 kampanya önereyim mi?

## Change

1. `app/brief.py` — `build_morning_brief(session) -> list[Reply]`:
   - Builds a `MediaContext` whose instruction is a fixed Turkish request ("Sabah özeti hazırla: bugünün randevuları,
     boş saatler, cevap bekleyen mesajlar, dünkü ciro. Sonunda tek bir somut öneri sun; öneriyi uygulamak için
     onay gerekiyorsa propose_actions ile plan kur.") and runs it through the SAME path as a normal message, i.e.
     `core.handle(session, "text", <instruction>)`. Result is either an answer (`reply_only`) or an approval card;
     both are fine — a card still needs the owner's `evet`.
   - Prefix the first reply with "Günaydın ☀️ " if it does not already start with a greeting.
2. `core.py` — nothing new is needed if (1) reuses `handle`; if you must add something, keep it to a small helper.
   Pending-card rules stay exactly as they are (a new instruction replaces an old pending card — that is fine).
3. `modal_app.py` — `@app.function(schedule=modal.Cron("0 6 * * *"), …)` (06:00 UTC = 09:00 Türkiye) named
   `morning_brief`, same image/secrets/volume as `worker`. For every sender in `settings.wa_allowed_senders`: load the
   session from the `sessions` Dict (create if missing), call `build_morning_brief`, send replies with the same
   delivery code the worker uses (reuse `app.worker` helpers; refactor delivery into a small function there only if
   needed and keep `process_inbound` behaviour identical), save the session back. One failure must not stop the
   others. Runs in the same single-container discipline: set `max_containers=1`.
   Note: WhatsApp only allows free text within 24 h of the owner's last message. If sending fails with that error,
   log it and skip (no template fallback in this task).
4. A manual trigger for the demo: `modal run modal_app.py::morning_brief` must work, and in the web UI a quick
   button "☀️ Sabah özeti" that calls a new `POST /api/brief` in `app/channels/web.py` (allowed to edit for this).

## Acceptance

- `python -m pytest -q` green; `tests/test_brief.py` covers: brief goes through `core.handle` (monkeypatched
  `agent.plan`), greeting prefix, a card produced by the brief is pending and needs `evet`, per-sender failure does
  not stop the loop (test the loop function directly, without Modal).
- Manual: `modal run modal_app.py::morning_brief` → the allow-listed phone gets the brief.
