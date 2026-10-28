# Task 07 — Bulk price changes, loyalty points, ads (agent side + mock)

Branch: `muse/07-bulk-loyalty-ads`. Allowed to edit: `app/tools/backend.py`, `app/actions.py`, `app/agent.py`,
`app/executor.py`, `app/approval.py`, `README.md` (rows in the "What the owner can ask" table), tests. Keep every
existing test green. Bump `MockBackend.SCHEMA` (rule 8).

## Goal

The owner can say:
- "Tüm çantalara %10 zam yap" / "Kesim hizmetlerini 50 lira artır" → bulk price change, preview on the card.
- "Ayşe Y.'ye 200 puan ekle, doğum günü" → loyalty points.
- "Reklamlar nasıl gidiyor?" → ads performance + suggestions (answer only).
- "En kötü giden reklamı durdur" / "Instagram kampanyasının bütçesini %15 artır" → ads action (needs approval).

The KEDD gateway already defines these endpoints (contract below); the mock must behave the same way.

## Read tools (agent.py TOOLS + backend methods, Mock + Kedd)

| Tool | Backend method | Kedd path |
|---|---|---|
| `preview_bulk_price(item_type?, category?, item_ids?, mode, value)` — mode `percent` or `amount` (TRY), value may be negative | `preview_bulk_price(...) -> {preview_id, items:[{id,name,old_price,new_price}], count}` | POST `/agent/catalog/bulk-price/preview` |
| `get_ads_performance(days=7)` | `ads_performance(days) -> {campaigns:[{id,name,platform,status,daily_budget,spend,impressions,clicks,results,cost_per_result}], totals}` | GET `/agent/ads/performance?days=` |
| `get_ads_suggestions()` | `ads_suggestions() -> {suggestions:[{campaign_id, kind, text}]}` | GET `/agent/ads/suggestions` |

Mock seeds: 3 ad campaigns per business (one clearly underperforming), 1–2 suggestions, `loyalty_points` on
customers. Preview in the mock: stores `{preview_id → items}` like segments, 15 min validity.

## Actions (actions.py schema + validation, executor, approval card)

| Action | Fields | Validation (code, not model) | Kedd path |
|---|---|---|---|
| `apply_bulk_price` | `preview_id`, `label` | `preview_id` from a `preview_bulk_price` call in the same run; preview has 1–200 items; every new price > 0; change per item within −50%…+50% | POST `/agent/catalog/bulk-price` |
| `adjust_loyalty` | `customer_id`, `points` (int), `reason`, `label` | `customer_id` from `find_customer` in the same run; integer; 1 ≤ abs(points) ≤ 1000 | POST `/agent/loyalty/adjust` |
| `ads_action` | `campaign_id`, `action` (`pause`/`resume`/`increase_budget`/`decrease_budget`), `delta_pct?`, `label` | `campaign_id` from `get_ads_performance` in the same run; budget actions need integer `delta_pct` 1–20 | POST `/agent/ads/action` |

Approval card lines: `💸 Toplu fiyat: 12 kalem (ilk 3: Deri Çanta 850→935 ₺, …)`, `⭐ Puan: Ayşe Y. +200 (doğum günü)`,
`📢 Reklam: <label>`. The model never sees a phone number (unchanged rule).

Mock writes must fail exactly like the gateway would: unknown/expired preview → `ValueError`, budget change on a paused
campaign → `ValueError`, points below zero clamp to 0 (KEDD behaviour).

System prompt: one short paragraph explaining the three flows; ads questions without an action go to `reply_only`
with numbers only from the tools; always preview before a bulk change.

## Acceptance

- `python -m pytest -q` green; new tests for: every validation rule above (accept + reject), preview expiry, mock
  write semantics, card lines, KeddBackend paths/bodies (mock `requests`, assert target and JSON).
- Manual (web UI, `/butik`): "Tüm elbiselere %10 zam yap" → card with before/after → `evet` → catalog shows new prices.
