# Task 09 — KEDD mode per conversation + public gateway contract

Branch: `muse/09-kedd-mode`. Allowed to edit: `app/core.py` (demo commands + `Session.get_backend` only),
`app/tools/backend.py` (`get_backend` + `KeddBackend`), `app/executor.py` (idempotency key only), `app/config.py` (nothing new expected — use the existing
`kedd_api_url`, `kedd_agent_secret`), new `docs/kedd-gateway-contract.md`, `README.md`, tests.

## 1. `/kedd` demo command

Today the backend is global (`BACKEND`). For the video the same WhatsApp chat must switch between the mock businesses
and the real KEDD demo business.

- `/kedd` → `session.business = "kedd"`, backend reset, pending/images/history cleared, reply
  "Tamam, artık KEDD'deki işletmen için çalışıyorum." (business name from `store_profile()` when reachable).
  `/butik` and `/salon` keep working and switch back.
- `Session.get_backend()`: for `business == "kedd"` build `KeddBackend(settings.kedd_api_url,
  settings.kedd_agent_secret, "+" + session.sender)` — the merchant is identified by the WhatsApp sender, never by a
  model-chosen value. If URL or secret is missing, raise a clear error; `/kedd` must answer
  "KEDD bağlantısı bu ortamda kapalı." instead of crashing.
- `KeddBackend` must not be pickled with an open `requests.Session`: implement `__getstate__`/`__setstate__` that drop
  and recreate it, and keep the secret out of `repr`. Add `SCHEMA`/`_schema` handling consistent with `MockBackend` so
  a stale session is re-created.
- Gateway errors arrive as `{error, message}` JSON: raise `ValueError(message)` so the executor shows the Turkish
  message and fails closed (no generic HTTPError text to the owner). 409 `in_progress` / `unknown_outcome` must say the
  operation may already have run and must not be retried automatically.
- Every POST already gets a fresh `Idempotency-Key`; make the executor pass a stable key per approved action instead
  (e.g. `<run_dir name>-<action index>`), so a retried delivery of the same approval cannot run twice. Add an optional
  `idempotency_key` parameter to the KeddBackend write methods; MockBackend ignores it.

## 2. `docs/kedd-gateway-contract.md` (public)

What a reader needs to implement or audit the other side, derived ONLY from `KeddBackend` and the security rules:
endpoint table (method, path, inputs, outputs), signature (`<ts>.<METHOD>.<path?query>.<rawBody>`, HMAC-SHA256 hex,
300 s window, single-use signatures), tenant resolution from the signed phone header (server-side mapping, never a
client-supplied business id), owner/admin role for writes, idempotency semantics (claim before run, 409 in_progress,
409 unknown_outcome), rate limits, audit, no customer phone numbers in responses, kill switch. Do NOT name KEDD
internal collections, files, functions or modules — describe behaviour only.

README: a short "KEDD integration" section — what is in this repo, what was built during the hackathon inside the
closed-source KEDD codebase (the gateway, ~3k lines), that it implements this contract, and that judges can run
everything against the mock, which follows the same contract.

## Acceptance

`python -m pytest -q` green with tests for: `/kedd` switch and fallback when unconfigured, phone comes from the session,
pickling a session holding a KeddBackend, gateway error → ValueError with the Turkish message, 409 handling, stable
idempotency key passed by the executor.
