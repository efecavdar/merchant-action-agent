# Task 08 — Template campaigns (what real WhatsApp marketing requires)

Branch: `muse/08-template-campaigns`. Allowed to edit: `app/actions.py`, `app/agent.py`, `app/executor.py`,
`app/approval.py`, `app/tools/backend.py`, tests, `README.md` (one sentence in "Design choices"). Bump `SCHEMA`.

## Why

WhatsApp only lets a business start a conversation with an **approved template**. Free-text campaigns work in the mock
but cannot work on KEDD. The KEDD gateway (K4) accepts `{segment_id, template, variables, dry_run?}` and fills the
customer name (`{{1}}`) itself, per recipient.

## Change

1. Backend method `campaign_templates() -> [{name, body, variables}]` and read tool `list_campaign_templates`.
   Mock returns these three (body text with `{{n}}` placeholders, Turkish, short; you write the bodies):
   `marketing_reengagement_promo_tr` {1 name, 2 code, 3 advantage}, `marketing_engagement_thankyou_promo_tr`
   {1 name, 2 code, 3 discount}, `marketing_welcome_offer_friendly_tr` {1 name}. Kedd: GET `/agent/campaign-templates`.
2. `send_campaign` action gains `template` and `variables` (dict of "2","3"… → string; never "1"). Validation:
   `template` must come from a `list_campaign_templates` call in the same run; exactly the template's variables except
   `1` must be present; each value ≤ 60 chars, no newline, no URL; the number/percent rule that today checks `message`
   applies to the rendered body (render with `{{1}}` = "Müşteri"). `message` stays allowed in mock mode only when
   `template` is absent (keeps the existing demo working); with `BACKEND=kedd` a template is required.
3. Approval card shows the rendered body (with `{{1}}` → "‹müşteri adı›") from the template facts — not model text.
4. Executor / KeddBackend: POST `/agent/campaigns` with `{name, segment_id, template, variables}`; mock renders per
   recipient and stores it like today.
5. Prompt: one paragraph — pick the template that matches the intent, fill only the variables, put the offer in
   `advantage`/`discount` (e.g. "hafta sonu tüm fularlarda %15 indirim"), code = short uppercase like `FULAR15`.

## Acceptance

`python -m pytest -q` green with tests for every validation rule, card rendering from template facts (a lying model
field must not reach the card), Kedd body, and the existing free-text mock flow still passing.
