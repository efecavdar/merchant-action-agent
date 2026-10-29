# Task 17 — Public demo runs on live KEDD (demo business, scoped credential)

Branch: `muse/17-live-kedd-demo`. **Start only when Efe says gateway K7 is deployed** and after tasks 15 and 16 are
merged. Allowed to edit: `app/channels/web.py`, `app/tools/backend.py`, `app/config.py` (two settings),
`modal_app.py` (the `demo` secret dict only), `app/static/**`, `tests/**`, `README.md` (the "Try it" line).
If `app/core.py` or `app/executor.py` seem to need a change, stop and write it in the PR.

## Goal

Judges talk to Çırak on the public page and it works on **real KEDD** — the synthetic demo business
`agent-demo-studio` in the live system — and they *see* KEDD change next to the chat.

## Security model (do not weaken; the gateway enforces the same rules server-side)

1. The public `demo` container gets a **separate demo credential**, never `KEDD_AGENT_SECRET` and never WhatsApp
   secrets. New settings: `kedd_demo_secret` (`KEDD_DEMO_SECRET`, `repr=False`) and `kedd_demo_phone`
   (`KEDD_DEMO_PHONE`). In `modal_app.py`, the demo secret dict gets exactly these extra keys from the local `.env`:
   `KEDD_API_URL`, `KEDD_DEMO_SECRET`, `KEDD_DEMO_PHONE` — same hand parser, same `modal.is_local()` guard. If any is
   missing, deploy the demo mock-only (no error) and log one line.
2. `KeddBackend` gets an optional `key_id` (`"main"` default, `"demo"`), sent as `X-Agent-Key-Id` and inside the
   signed string exactly as `docs/kedd-gateway-contract.md` says after K7. In public mode the only way to build a
   `KeddBackend` is `get_backend("kedd-demo")` → `KeddBackend(url, kedd_demo_secret, kedd_demo_phone, key_id="demo")`.
   Test: with `KEDD_AGENT_SECRET` set in env and public mode on, no code path uses it (assert on the signer input).
3. Public mode default business = `kedd-demo` ("Stüdyo · canlı KEDD" badge). `/butik` and `/salon` stay as offline
   fallbacks. `/kedd` stays refused. If the gateway returns 503/5xx or times out (5 s) on the first call of a
   session, switch that session to mock `salon` with one Turkish+English line saying so.
4. Writes that touch the outside world never leave the demo: `send_campaign` and `publish_social_post` (task 14) in
   public mode are executed as **preview only** (`dry_run: true` for campaigns; social: show the visual + caption,
   status "demo: yayınlanmadı"). The gateway also refuses them for the demo key — this is defence in depth, keep both.
5. All task 13 limits stay. Add: at most 3 approved plans per IP per hour (an "evet" counts), so one visitor cannot
   flood the shared demo business.

## "Live in KEDD" panel

Next to the phone (below it on mobile): **"KEDD'de şu an / Live in KEDD"**, read-only, refreshed after every
executed plan and every 30 s while the tab is visible.
- `GET /api/kedd-state` (server side, demo key only, cached 10 s for everyone, counts as free): catalog (name, price,
  sale price, highlighted when changed in the last 10 min), next 8 appointments (time, service, staff, customer as
  first name + initial), last 10 agent writes from `/agent/report` (time, capability, ok/error).
- No phone numbers, no full customer names, no ids in the HTML. Shared by all visitors (it is one business) — say so
  in a hint: "Everyone using this demo shares one KEDD business."

## Acceptance

`python -m pytest -q` green with tests for: demo key used in public mode and main secret never; `X-Agent-Key-Id`
in headers and in the signed string; campaign/social forced to preview in public mode (executor called with dry run;
no publish call); fallback to mock on 503; `/api/kedd-state` strips PII (assert no `+90`, no 10+ digit runs, last
names reduced to an initial); per-IP approved-plan cap; cache (one gateway call for two quick requests).
