# KEDD agent gateway — public contract

What the `KeddBackend` adapter (`app/tools/backend.py`) expects from the other side.
This document describes wire behaviour only: endpoints, authentication, errors and safety
rules. It names no KEDD-internal collections, files, functions or modules.

The mock backend in this repo (`MockBackend`) implements the same contract against synthetic
data, so everything below can be exercised without KEDD: `python -m pytest -q`.

## 1. Transport and authentication

* Base URL and shared secret come from `KEDD_API_URL` / `KEDD_AGENT_SECRET`. Every request
  carries three headers plus `Content-Type: application/json`:
  * `X-Agent-Phone` — the merchant's WhatsApp number in E.164 (`+905…`).
  * `X-Agent-Timestamp` — unix seconds. Requests older or newer than 300 s are rejected.
  * `X-Agent-Signature` — hex HMAC-SHA256(secret, `"<ts>.<METHOD>.<path?query>.<rawBody>"`).
    `path?query` is the request target exactly as sent on the wire (query string included,
    e.g. `/agent/appointments?customer_name=Ay%C5%9Fe&day=2026-10-29`); `rawBody` is the exact
    request bytes (`""` for GET). Comparison is constant-time.
* Signatures are single-use: a replayed signature is rejected even inside the time window.
* The gateway resolves the business from its own phone→business mapping. The client never
  sends a business id, and there is no way to ask for another merchant's data.
* Unknown or disabled phone numbers are rejected, as are suspended businesses.

## 2. Endpoints

Reads are `GET`; everything else is `POST`. Every `POST` — including the two read-only ones
(marked R below) — must carry an `Idempotency-Key` header. Shapes mirror the `Backend`
interface: products and services are one catalog (`type` = `product` | `service`), a discount
is a sale-price window on catalog items.

### Reads

| Method | Path | Inputs | Output |
|---|---|---|---|
| GET | `/agent/profile` | — | store profile: name, city, sector, channels, tone, currency, business hours, staff, plus today's counts (customers by loyalty, catalog size) |
| GET | `/agent/catalog` | `type` (optional) | catalog items: id, type, name, price, currency, category, description, image, duration (services), sale window |
| GET | `/agent/schedule` | `day` (YYYY-MM-DD) | that day's confirmed appointments plus free slots per staff member |
| GET | `/agent/customers` | `name` | up to 10 matches: id, name, last visit, visit count. No phone numbers |
| GET | `/agent/daily-summary` | `day` | appointments, revenue total + by category, new customers/leads, unanswered messages, campaigns sent, catalog changes |
| GET | `/agent/appointments` | `customer_name`, `day` (optional) | appointments: id, customer, service, staff, start/end, status |
| GET | `/agent/ads/performance` | `days` (default 7) | ad campaigns with spend/impressions/clicks/results/cost-per-result, plus totals |
| GET | `/agent/ads/suggestions` | — | suggested ad actions with reasons |
| GET | `/agent/campaign-templates` | — | approved templates: name, body, variables. `{{1}}` is always the customer name and is filled per recipient by the gateway |
| GET | `/agent/report` | — | what the run changed: items added, discounts, campaigns, appointment changes |
| POST (R) | `/agent/segments` | `{inactive_days, min_visits, tags}` | `{segment_id, criteria, count, excluded_no_consent, sample}` — the sample is at most 5 entries (name + last visit), never phone numbers |
| POST (R) | `/agent/catalog/bulk-price/preview` | `{item_type, category, item_ids, mode, value}` (`mode`: `percent` \| `amount`) | `{preview_id, items: [{id, name, old_price, new_price}], count}` — changes nothing |

### Writes (owner/admin role required)

| Method | Path | Inputs | Output |
|---|---|---|---|
| POST | `/agent/catalog` | `{type, name, price, category, description, image_path, duration_minutes}` | the created item |
| POST | `/agent/discounts` | `{name, percent, item_ids, starts, ends}` | the discount plus the resulting sale prices |
| POST | `/agent/campaigns` | `{name, segment_id, template, variables}` or `{name, segment_id, message, visual_path}` | the sent campaign, incl. recipient count |
| POST | `/agent/appointments` | `{customer_id, item_id, staff, starts}` | the booked appointment; overbooking and out-of-hours slots fail |
| POST | `/agent/appointments/reschedule` | `{appointment_id, starts, staff?}` | the moved appointment |
| POST | `/agent/appointments/cancel` | `{appointment_id, reason?}` | the cancelled appointment |
| POST | `/agent/catalog/bulk-price` | `{preview_id}` | applied rows `{id, old_price, new_price}`; unknown/expired previews fail |
| POST | `/agent/loyalty/adjust` | `{customer_id, points, reason}` | `{customer_id, name, old_points, points, delta, reason}` |
| POST | `/agent/ads/action` | `{campaign_id, action, delta_pct?}` (`pause` \| `resume` \| `increase_budget` \| `decrease_budget`) | the updated campaign |

Writes re-validate every limit server-side (discount caps, recipient caps, slot clashes,
business hours) and never trust the client's checks.

## 3. Errors

Failures return `{error, message}` JSON. `message` is Turkish and safe to show the merchant;
`error` is a stable machine code. The adapter raises the message as `ValueError` and fails
closed: after a failed step, later steps (like sending messages) do not run.

| Status | `error` | Meaning |
|---|---|---|
| 400 | `idempotency_key_required`, `invalid_request` | missing/unusable key, or the request fails validation |
| 401 | `unauthorized` | bad/missing signature, timestamp outside the 300 s window, replayed signature |
| 403 | `forbidden`, `suspended` | unknown/disabled phone, suspended business, or missing role/module |
| 404 | `not_found` | unregistered path |
| 409 | `in_progress` | this key is being executed right now — it may already be running, do not retry |
| 409 | `unknown_outcome` | this key's execution ended ambiguously — it may already have run, do not retry automatically |
| 415 | `unsupported_media_type` | POST without `Content-Type: application/json` |
| 429 | `rate_limited` | per-business read/write caps exceeded |
| 503 | `disabled` | the gateway is switched off (globally or for this merchant) |
| 500 | `internal` | unexpected failure, no details leaked |

## 4. Idempotency

* Claim-before-run: the first request with a key claims it; a replay while it runs gets 409
  `in_progress`; a replay after success gets the stored result unchanged.
* A write that ends in a 500 is recorded as `unknown_outcome`: replays get 409 and the
  operation is never retried automatically, because the side effect may have happened.
* The agent sends one stable key per approved action (`<run_dir>-<action index>`), so a
  retried delivery of the same approval cannot execute twice.

## 5. Abuse controls and privacy

* Separate hourly caps for reads and writes per business (429 when exceeded).
* Every call is audit-logged server-side: business, capability, read/write, a PII-free
  request summary, result, and duration.
* Customer phone numbers never appear in responses; segments are counts plus at most five
  name-only samples.
* Kill switch: disabling one merchant's phone mapping stops that merchant; disabling the
  gateway stops everyone (503).
