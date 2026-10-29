# Task 20 — Wire campaigns, loyalty and ads to the live KEDD gateway

Branch: `muse/20-kedd-writes`. Allowed to edit: `app/tools/backend.py` (KeddBackend only), `app/agent.py` (tool list
and tool descriptions only), `app/approval.py` (card fact lines only), `tests/**`, `tests/fixtures/kedd/**`,
`docs/kedd-gateway-contract.md`. If `app/core.py`, `app/executor.py` or `app/actions.py` seem to need a change, stop
and write it in the PR. Start after `git pull` (task 19 merged).

## Goal

In KEDD mode `send_campaign`, `adjust_loyalty` and `ads_action` still raise "henüz bağlı değil". Make them call the
live gateway, keeping every existing rule: IDs come from tool results in the same run, the card is rendered from
facts, stable idempotency keys, Turkish gateway messages shown as-is.

## Gateway contract (live)

- `POST /agent/campaigns` `{segment_id, template, variables: {code?, advantage?, discount?}, dry_run?}` →
  `{dry_run, template, segment_id, recipients, sent, skipped: {reason: count}, failed, failed_by_code, template_text?}`.
  `template_text` only on dry runs. Errors to show verbatim: 409 `segment_drift`, `segment_used`, `quiet_hours`,
  `quota_exceeded`; 400 `invalid_variable`. A segment can be sent once; a retry with the same idempotency key returns
  the stored result.
- `GET /agent/loyalty/members?name=` (new, gateway K5.1) → `{members: [{member_id, name, points, tier?}], truncated}`,
  max 10, no phone numbers.
- `POST /agent/loyalty/adjust` `{member_id, delta (int, non-zero, |Δ| ≤ 1000), reason?}` →
  `{member_id, delta, points_before, points_after}`.
- `POST /agent/ads/action` `{campaign_id, action: "pause"|"resume"|"set_budget", budget? (absolute, only with
  set_budget), reason (required)}` → `{campaign_id, action, budget_before?, budget_after?}`. Server limits: budget step
  ≤ ±20 %, ≤ 3× the original, ≤ 2 budget changes per business per day (409 `too_many_budget_ops`).

## Change

1. `send_campaign`: template campaigns only (already required in KEDD mode). Before the approval card, the plan step
   runs a **dry run** so the card shows the real `template_text` with the first customer's name replaced by
   "‹müşteri adı›" and the real eligible count ("23 kişiye gidecek, 4 kişi izinsiz olduğu için atlanacak"). If the
   dry run is not reachable from the backend layer without a core change, put the dry-run call in a read tool the
   planner must call (`preview_campaign`) and render the card line from that tool's fact. Execution sends with
   `dry_run: false` and reports sent / skipped / failed from the response only.
2. Loyalty: a read tool `find_loyalty_member(name)` (KEDD: the new endpoint; mock: existing customers with points).
   `adjust_loyalty` uses a `member_id` that came from that tool in the same run. Card: name, points before → after.
3. Ads: `ads_action` maps the plan's `delta_pct` to an absolute `budget` from `/agent/ads/performance` data of the
   same run (round to 2 decimals; refuse in code before calling if |delta_pct| > 20). `reason` = the owner's
   sentence, trimmed to 200 chars. Card: campaign name, budget before → after, or pause/resume.
4. Demo key (public page): unchanged. Campaign is preview-only, ads stay refused, loyalty is allowed.
5. Fixtures for every new response shape; `MockBackend` keeps working (bump `SCHEMA` if you touch its attributes).

## Acceptance

`python -m pytest -q` green, with tests for: request bodies and idempotency keys of the three writes; each 409 code
surfaces its Turkish message; card lines for campaign (template text + counts), loyalty (before → after) and ads
(budget before → after); a member_id or campaign_id not seen in the run is rejected; delta_pct 25 refused without a
network call; demo key never calls `/agent/campaigns` with `dry_run: false` or `/agent/ads/action`.
