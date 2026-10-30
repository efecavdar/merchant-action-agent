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
  * `X-Agent-Signature` — lowercase hex HMAC-SHA256(secret,
    `"<ts>.<METHOD>.<path?query>.<X-Agent-Phone>.<Idempotency-Key or empty>.<rawBody>"`).
  * `X-Agent-Key-Id` (optional) — `main` (default) or `demo`. Selects which secret the signature is checked
    against. The `demo` key is bound server-side to one phone and one synthetic business, can read, and can only
    make the reversible writes on an allow-list (no messages to customers, no social posts, no ads changes).
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
| GET | `/agent/customers` | `name` | up to 10 matches: id, name, last visit, visit count, `company?` (also matched against). No phone numbers. Gateway K12 adds the company match + field — agreed shape, pending deploy |
| GET | `/agent/loyalty/members` | `name` | up to 10 matches: `{members: [{member_id, name, points, tier?}], truncated}`. No phone numbers. Gateway K5.1 — agreed shape, pending deploy |
| GET | `/agent/daily-summary` | `day` | appointments, revenue total + by category, new customers/leads, unanswered messages, campaigns sent, catalog changes |
| GET | `/agent/appointments` | `customer_name`, `day` (optional) | appointments: id, customer, service, staff, start/end, status |
| GET | `/agent/ads/performance` | `days` (default 7) | ad campaigns with spend/impressions/clicks/results/cost-per-result, plus totals |
| GET | `/agent/ads/suggestions` | — | suggested ad actions with reasons |
| GET | `/agent/campaign-templates` | — | approved templates: name, body, variables. `{{1}}` is always the customer name and is filled per recipient by the gateway |
| GET | `/agent/report` | — | agent activity counters for the window: `{window_hours, total, by_capability: {name: {ok, error}}}`. Names are dotted capabilities (`catalog.create`, `appointments.reschedule`, `profile`, …) and include reads as well as writes; clients filter what they display |
| POST (R) | `/agent/segments` | `{inactive_days, min_visits, tags}` | `{segment_id, criteria, count, excluded_no_consent, sample}` — the sample is at most 5 entries (name + last visit), never phone numbers |
| POST (R) | `/agent/catalog/bulk-price/preview` | `{item_type, category, item_ids, mode, value}` (`mode`: `percent` \| `amount`) | `{preview_id, items: [{id, name, old_price, new_price}], count}` — changes nothing |
| GET | `/agent/leads` | `stage`, `name` (optional) | `{leads: [{id, name, source, stage, last_contact\|null, value?, company?}], truncated}` (≤ 20). Display names only, no contact details. Gateway K11 — agreed shape, pending deploy; K12 adds the company match + field |
| GET | `/agent/tasks` | `status` = `open` \| `overdue` \| `completed` \| `all` | `{tasks: [{id, title, due_date, status, overdue, customer_id?, customer_name?, lead_id?, lead_name?}], truncated}` (≤ 30). Gateway K11 — agreed shape, pending deploy |
| GET | `/agent/insights` | `days` 1–90 (default 7) | `{days, period, prev_period, revenue: {paid_total, paid_count, prev_paid_total, diff}, top_items: [{name, revenue, count}], new_customers, funnel: {new, contacted, qualified, proposal, won, lost}, appointments: {total, completed, cancelled, occupancy_pct}}`. Gateway K11 — agreed shape, pending deploy |
| GET | `/agent/chatbot/knowledge` | `q` | `{entries: [{id, question, answer}], truncated}` (≤ 20). Gateway K12 — agreed shape, pending deploy |
| GET | `/agent/inbox` | `q`, `unread_only` (optional) | `{items: [{id, customer, last, at}], truncated}` (≤ 20). `customer` = first name + initial, never a phone number; `last` is masked. Gateway K13 — agreed shape, pending deploy |
| GET | `/agent/inbox/thread` | `conversation_id`, `limit` 1–50, `before` (optional) | `{messages: [{from: customer\|business, text, at}], has_more}` chronological, masked. Gateway K13 — agreed shape, pending deploy |

### Writes (owner/admin role required)

| Method | Path | Inputs | Output |
|---|---|---|---|
| POST | `/agent/catalog` | `{type, name, price, category, description, image_path, duration_minutes}` | the created item |
| POST | `/agent/discounts` | `{name, percent, item_ids, starts, ends}` | the discount plus the resulting sale prices |
| POST | `/agent/campaigns` | `{segment_id, template, variables: {code?, advantage?, discount?}, dry_run?}` | `{dry_run, template, segment_id, recipients, sent, skipped: {reason: count}, failed, failed_by_code, template_text?}`. `template_text` only on dry runs (`{{1}}` filled with the first customer's name). A segment can be sent once; a retry with the same Idempotency-Key returns the stored result |
| POST | `/agent/appointments` | `{customer_id, item_id, staff, starts}` | the booked appointment; overbooking and out-of-hours slots fail |
| POST | `/agent/appointments/reschedule` | `{appointment_id, starts, staff?}` | the moved appointment |
| POST | `/agent/appointments/cancel` | `{appointment_id, reason?}` | the cancelled appointment |
| POST | `/agent/catalog/bulk-price` | `{preview_id}` | applied rows `{id, old_price, new_price}`; unknown/expired previews fail |
| POST | `/agent/loyalty/adjust` | `{member_id, delta (int, non-zero, |Δ| ≤ 1000), reason?}` | `{member_id, delta, points_before, points_after}` |
| POST | `/agent/ads/action` | `{campaign_id, action: "pause" \| "resume" \| "set_budget", budget? (absolute, only with `set_budget`), reason (required)}` | `{campaign_id, action, budget_before?, budget_after?}`. Server limits: budget step ≤ ±20 %, ≤ 3× the original, ≤ 2 budget changes per business per day (409 `too_many_budget_ops`) |
| POST | `/agent/customers` | `{name 2–80, phone?, email?, tags? ≤10×30, note? ≤500}` | `{id, name}`. Marketing consent is always false. 409 `customer_exists` (+`customer_id`) when the customer is already registered; demo key + phone → 400. Gateway K11 — agreed shape, pending deploy |
| POST | `/agent/customers/update` | `{customer_id, name?, add_tags?, remove_tags?, note?}` | `{id, name, tags}`. Phone, e-mail and consent cannot change. 409 `customer_merged` / `customer_archived`. Gateway K11 — agreed shape, pending deploy |
| POST | `/agent/leads` | `{name 2–80, phone?, email?, estimatedValue? ≥0, tags?, note?}` | `{id, name}`. 409 `lead_exists` (+`lead_id`). Gateway K11 — agreed shape, pending deploy |
| POST | `/agent/leads/stage` | `{lead_id, stage ∈ new\|contacted\|qualified\|proposal\|won\|lost}` | `{id, stage, prev_stage}`. Gateway K11 — agreed shape, pending deploy |
| POST | `/agent/leads/note` | `{lead_id, note ≤500}` | `{id, note_id}`. Gateway K11 — agreed shape, pending deploy |
| POST | `/agent/tasks` | `{title ≤120, due_date ISO (date or datetime), customer_id?, lead_id?}` | `{id, title, due_date, status}`. Gateway K11 — agreed shape, pending deploy |
| POST | `/agent/tasks/complete` | `{task_id}` | `{id, status}`. Gateway K11 — agreed shape, pending deploy |
| POST | `/agent/appointments/complete` | `{appointment_id, amount? (0 < x ≤ 100000), method?: cash\|card\|transfer\|other, note? ≤500}` | `{id, status: "completed", revenue_id?, amount?}` (no revenue fields for free/package completions). 404 `not_found`, 409 `already_cancelled` / `already_completed`, 400 `future_appointment`, `event_reservation`, `no_credits`, `subscription_inactive`, `package_expired`, `service_not_allowed`. Gateway K12 — agreed shape, pending deploy |
| POST | `/agent/quotes` | `{customer_id \| lead_id (exactly one), items: [{catalog_item_id, qty 1–100, unit_price? ≥0}], valid_days? 1–60, note? ≤500}` | `{id, total, status: "draft"}`. Never sent to anyone. Server rejects a unit price below half the catalog price. Gateway K12 — agreed shape, pending deploy |
| POST | `/agent/chatbot/knowledge` | `{question ≤200, answer ≤1000}` | `{id}`. 409 `no_assistant` when the business has no chatbot assistant. Gateway K12 — agreed shape, pending deploy |
| POST | `/agent/inbox/reply` | `{conversation_id, text 1–1000}` | `{message_id}`. 404 `not_found`, 409 `window_closed` (24 h window) / `repeat` (60 s) / `no_phone`, 429 `rate_limited`, 502 `send_failed`. A retry with the same idempotency key does not send twice. Gateway K13 — agreed shape, pending deploy |

Writes re-validate every limit server-side (discount caps, recipient caps, slot clashes,
business hours) and never trust the client's checks.

## 3. Errors

Failures return `{error, message}` JSON. `message` is Turkish and safe to show the merchant;
`error` is a stable machine code. The adapter raises the message as `ValueError` and fails
closed: after a failed step, later steps (like sending messages) do not run.

| Status | `error` | Meaning |
|---|---|---|
| 400 | `idempotency_key_required`, `invalid_request` | missing/unusable key, or the request fails validation |
| 400 | `invalid_variable` | a campaign variable the template does not declare |
| 401 | `unauthorized` | bad/missing signature, timestamp outside the 300 s window, replayed signature |
| 403 | `forbidden`, `suspended` | unknown/disabled phone, suspended business, or missing role/module |
| 404 | `not_found` | unregistered path |
| 409 | `in_progress` | this key is being executed right now — it may already be running, do not retry |
| 409 | `unknown_outcome` | this key's execution ended ambiguously — it may already have run, do not retry automatically |
| 409 | `segment_drift`, `segment_used`, `quiet_hours`, `quota_exceeded` | the campaign segment changed, was already sent, is in quiet hours, or the quota is spent |
| 409 | `too_many_budget_ops` | more than 2 ad budget changes for this business today |
| 409 | `customer_exists`, `lead_exists` | the customer/lead is already registered; the payload carries the existing `customer_id` / `lead_id` so the client can offer it |
| 409 | `customer_merged`, `customer_archived` | the customer was merged into another record or archived; the update is refused |
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
