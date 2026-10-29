# Task 27 — Company-name search, complete + revenue, quote drafts, teach the chatbot

Branch: `muse/27-money-content`. Allowed to edit: `app/actions.py`, `app/agent.py`, `app/executor.py`,
`app/approval.py`, `app/i18n.py`, `app/tools/backend.py`, `tests/**`, `tests/fixtures/kedd/**`,
`docs/kedd-gateway-contract.md`, `app/static/coverage.json`. Same rules as task 26 (ids from the same run, card from
facts, idempotency keys, owner language, progress lines, mock parity, `SCHEMA` bump if needed).

## Gateway contract (gateway K12)

- `GET /agent/customers?name=` and `GET /agent/leads?name=` also match the company name; rows gain `company?`.
  Show it everywhere a customer/lead is named: "Murat Demir (Ada Kafe)".
- `POST /agent/appointments/complete` `{appointment_id, amount? (0 < x ≤ 100000), method?: cash|card|transfer|other,
  note? ≤500}` → `{id, status: "completed", revenue_id?, amount?}` (no revenue fields for free/package completions).
  Errors to show verbatim: 404 `not_found`, 409 `already_cancelled` / `already_completed`, 400 `future_appointment`,
  `event_reservation`, package codes (`no_credits`, `subscription_inactive`, `package_expired`, `service_not_allowed`).
- `POST /agent/quotes` `{customer_id | lead_id (exactly one), items: [{catalog_item_id, qty 1–100, unit_price? ≥0}],
  valid_days? 1–60, note? ≤500}` → `{id, total, status: "draft"}`. Never sent to anyone. Server rejects a unit price
  below half the catalog price.
- `POST /agent/chatbot/knowledge` `{question ≤200, answer ≤1000}` → `{id}`; 409 `no_assistant`.
  `GET /agent/chatbot/knowledge?q=` → `{entries: [{id, question, answer}], truncated}` (≤20).

## Change

1. Actions: `complete_appointment` (appointment id from `find_appointments`/schedule in the same run; amount defaults
   to the service price from the run's catalog facts; method optional), `create_quote_draft` (customer or lead from the
   run; catalog ids from the run; client-side refuse unit price < 50 % of catalog price before any call),
   `teach_chatbot` (question/answer; contact-detail rules apply to both; refuse if the answer contains a price that is
   not in the catalog facts of the run — the chatbot must not learn wrong prices).
2. Read tool: `search_chatbot_knowledge(q)` so the planner can avoid duplicates ("bu zaten öğretilmiş").
3. Card lines from facts: "✔️ Randevu tamamlandı: Murat Demir (Ada Kafe) — Kurulum + Eğitim, 2.500 ₺ havale",
   "📄 Teklif taslağı: Lavanta Spa — KEDD Büyüme ×1 + Kurulum ×1 = 6.000 ₺ (gönderilmez, taslak)",
   "🧠 Chatbot öğrenecek: “Kurulum ne kadar sürer?” → “1 iş günü.”" (answer first 120 chars). English via task 23.
4. Morning brief: include today's completed appointments count and revenue when `get_insights(days=1)` has them.
5. `coverage.json`: "Complete appointments + revenue", "Quote drafts", "Teach the AI chatbot" live.

## Acceptance

`python -m pytest -q` green with tests for: request bodies + idempotency keys; every listed error surfaced in
Turkish; amount default from catalog facts; quote floor refused before network; wrong price in a chatbot answer
refused; company name shown on cards; progress lines; mock parity (mock completion writes a revenue row the insights
see; mock quotes; mock knowledge).
