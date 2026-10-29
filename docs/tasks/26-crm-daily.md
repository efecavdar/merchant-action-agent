# Task 26 — CRM and daily work: customers, leads, tasks, insights

Branch: `muse/26-crm-daily`. Allowed to edit: `app/actions.py`, `app/agent.py`, `app/executor.py`, `app/approval.py`,
`app/i18n.py`, `app/tools/backend.py`, `tests/**`, `tests/fixtures/kedd/**`, `docs/kedd-gateway-contract.md`,
`app/static/coverage.json`. Keep every existing rule: the model proposes, code executes; IDs come from tool results
in the same run; the card renders from facts; stable idempotency keys; Turkish gateway messages shown verbatim;
owner language (task 23); live progress lines (task 25) for every new action. Bump `MockBackend.SCHEMA` if you add
attributes.

## Gateway contract (gateway K11)

- `POST /agent/customers` `{name 2–80, phone?, email?, tags? ≤10×30, note? ≤500}` → `{id, name}`;
  409 `{error: "customer_exists", customer_id}`; demo key + phone → 400. Marketing consent is always false.
- `POST /agent/customers/update` `{customer_id, name?, add_tags?, remove_tags?, note?}` → `{id, name, tags}`;
  409 `customer_merged` / `customer_archived`. Phone, e-mail and consent cannot change.
- `GET /agent/leads?stage=&name=` → `{leads: [{id, name, source, stage, last_contact|null, value?}], truncated}` (≤20).
- `POST /agent/leads` `{name 2–80, phone?, email?, estimatedValue? ≥0, tags?, note?}` → `{id, name}`;
  409 `{error: "lead_exists", lead_id}`.
- `POST /agent/leads/stage` `{lead_id, stage ∈ new|contacted|qualified|proposal|won|lost}` → `{id, stage, prev_stage}`.
- `POST /agent/leads/note` `{lead_id, note ≤500}` → `{id, note_id}`.
- `POST /agent/tasks` `{title ≤120, due_date ISO, customer_id?, lead_id?}` → `{id, title, due_date, status}`.
- `POST /agent/tasks/complete` `{task_id}` → `{id, status}`.
- `GET /agent/tasks?status=open|overdue|completed|all` → `{tasks: [{id, title, due_date, status, overdue, customer_id?,
  customer_name?, lead_id?, lead_name?}], truncated}` (≤30).
- `GET /agent/insights?days=1–90` → `{days, period, prev_period, revenue: {paid_total, paid_count, prev_paid_total,
  diff}, top_items: [{name, revenue, count}], new_customers, funnel: {new, contacted, qualified, proposal, won, lost},
  appointments: {total, completed, cancelled, occupancy_pct}}`.

## Change

1. Read tools: `find_leads(stage?, name?)`, `list_tasks(status?)`, `get_insights(days?)`. Insights also feed the
   reply-only answers ("bu hafta nasıldı?", "huni ne durumda?") and the morning brief (one line: week revenue vs
   previous week, open/overdue task count).
2. Actions: `create_customer`, `update_customer`, `create_lead`, `set_lead_stage`, `add_lead_note`, `create_task`,
   `complete_task`. Validation: names/notes/tags within the gateway limits (reuse the contact-detail rules for notes
   and tags: no URLs, e-mails or phone numbers in notes/tags); `customer_id` / `lead_id` / `task_id` must come from a
   tool in the same run (customers via `find_customers`, leads via `find_leads`, tasks via `list_tasks`, or an id
   created earlier in the same plan via a `ref`); `due_date` not in the past, ≤ 1 year; a plan may not combine
   `create_customer` with `send_campaign` to that customer (consent is false anyway — say so in the card note).
3. Card lines from facts: "👤 Yeni müşteri: Ada Kafe — Murat (etiket: kafe)", "🏷️ Ada Kafe: +etiket vip, not eklendi",
   "🧲 Yeni lead: Lavanta Spa — tahmini 3.500 ₺", "📈 Lavanta Spa: teklif → kazanıldı", "📝 Not: …" (first 80 chars),
   "⏰ Görev: Ada Kafe'yi ara — Cuma 10:00", "✅ Görev tamamlandı: …". English via task 23.
4. Mock backend: the same shapes in memory (leads with stages, tasks with due dates, insights computed from the mock's
   revenue/appointments), so the public demo's mock fallback and the tests work offline.
5. `coverage.json`: add "Customers & leads (CRM)", "Tasks & reminders", "Weekly insights" as live.

## Acceptance

`python -m pytest -q` green with tests for: every request body + idempotency key; 409 `customer_exists` /
`lead_exists` surfaced in Turkish and offering the existing record; ids not seen in the run rejected; contact
details in notes/tags rejected; card lines in TR and EN; progress lines; mock parity for all new methods; demo key
creates a customer without a phone.
