# Task 11 — Align KeddBackend with the live gateway's response shapes

Branch: `muse/11-kedd-shapes`. Allowed to edit: `app/tools/backend.py` (`KeddBackend` only), tests, new
`tests/fixtures/kedd/*.json`. **Run this task in Standard mode** (it needs to read the KEDD gateway code in
`~/Projects/kedd-agent-gateway/functions/src/agent/` to learn the response shapes; that code must never be sent in
contributor mode). Do NOT copy KEDD code, file paths, collection names or internal identifiers into this repo.

## Problem

The agent and the approval card were built against `MockBackend`'s return shapes. The live gateway returns different
ones, e.g. `store_profile()` → `{"business": {"name", "city", "sector", "hours": {...}}}` instead of a flat dict with
`business_name`, `daily_summary()` → `{"revenue": {"paidTotal"}, "missedWhatsapp", ...}`. Code that reads fields
(card facts: `matches`, `items`, `appointments`, `preview_id`/`items[].old_price/new_price`, `campaigns`,
`segment_id`/`count`, template `body`/`variables`; `/kedd` greeting: `business_name`) breaks silently.

## Change

`KeddBackend` becomes the adapter: every method returns exactly the shape `MockBackend` returns for the same method
(same keys, same types, same wrapping — e.g. `list_catalog()` returns a list, not `{"items": [...]}`), mapping from the
gateway's real response. Where the gateway has extra useful fields, keep them under the mock's keys only if they
mean the same thing; otherwise drop them. Never pass a phone number through. Write methods map the mock's argument
names onto the gateway's real request bodies (e.g. K3 expects `{customer_id, catalog_item_id, date, time, staff_id?}`
— split the agent's ISO `starts` into date/time; staff names must be resolved to the gateway's staff id via the
profile's staff list).

Record one sanitized fixture per endpoint from the gateway's own tests/types (synthetic values only) in
`tests/fixtures/kedd/`, and a parametrized test that feeds each fixture through `KeddBackend` (mocked HTTP) and
asserts the result has the same keys/types as the corresponding `MockBackend` call. Also assert no `+90`/phone fields.

## Acceptance

`python -m pytest -q` green. A shape-parity test exists for every `Backend` protocol method. PR lists any method
where the gateway cannot provide something the mock provides (say what the agent will see instead).
