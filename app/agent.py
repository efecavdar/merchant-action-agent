"""Orchestrator: Muse Spark plans with tools, then hands a proposal to a human.

Design (deliberately conservative — this agent sends messages to real customers in production):

  Phase 1  PLAN     Muse Spark reads the MediaContext, calls READ-ONLY tools
                    (business profile, catalog, customer segments, schedule, daily summary) and
                    finishes with ONE of:
                      * `propose_actions` — a structured plan that needs approval, or
                      * `reply_only`      — a plain answer when nothing has to be written
                                            ("Bugün ne oldu?", "yarın kim var?").
  Gate     VALIDATE Code validates the plan (schema + business rules). Problems are sent
                    back to the model, which revises. Max 3 revisions.
  Gate     APPROVE  A human sees the plan as a WhatsApp-style card and says yes / no.
  Phase 2  EXECUTE  Deterministic code executes the approved actions (executor.py).
                    The model never calls a write tool directly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from . import meta_client as meta
from .actions import PROPOSAL_SCHEMA, validate_proposal
from .config import settings
from .pipeline import MediaContext
from .tools.backend import Backend, KeddBackend

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are "Çırak", the operations assistant of a small Turkish business (a boutique, a hair salon,
a café...). The owner sends you an instruction — usually a WhatsApp voice note, sometimes with product photos that
have already been detected and described for you. Your job is to turn the instruction into a concrete, safe plan,
or to answer a question from the business data.

Rules:
- Always start with get_store_profile (sector, today's date, staff, tone). Never plan blind.
- Two ways to finish, exactly one of them, exactly once:
  * propose_actions — when something must be written: catalog, discount, campaign, appointment.
  * reply_only — when the owner only asks something ("bugün ne oldu?", "yarın boşluk var mı?", "Ayşe'nin randevusu
    ne zaman?"). Answer in Turkish, 3-6 short lines, informal (sen), numbers ONLY from tool results, one emoji max.
    For "bugün ne oldu / nasıl geçti" call get_daily_summary(today) and summarise: appointments, revenue, new
    customers/leads, unanswered messages (name them), campaigns, catalog changes. Skip zero items.
- Catalog items are products OR services (a service has a duration). You can add an item from a detected photo
  (temp_id) or from the owner's words alone (ref). "Keratin bakımı ekle, 90 dakika, 2500 lira" is a complete
  service; "yeni ipek fular koleksiyonu, tanesi 850" is a product even without a photo.
- Prices: use exactly what the owner said. If one price is given for several items, apply it to each. Never guess
  a price that was not stated; if it is missing, leave the item out and say so in summary_tr. Never apply a
  discount to unrelated existing items just because a new item could not be created.
- Discounts: integers only, dates as ISO (YYYY-MM-DD). "hafta sonu" = the coming Saturday–Sunday based on
  today's date. "yarın" = tomorrow. Use get_schedule when the owner talks about empty hours.
- Appointments: resolve the customer with find_customer (name → id), the service with list_catalog, the existing
  appointment with find_appointments, and check the slot with get_schedule before proposing. If several customers
  match a name, do not guess: reply_only and ask which one. Times are ISO datetimes (2026-10-31T15:00). Every
  appointment action carries a `label` the owner can read ("Ayşe Y. — Saç Kesimi, Elif, Cuma 15:00").
- Audience: call find_customers whenever the owner wants to notify customers; never invent a segment id.
  "uzun süredir gelmeyenler" ≈ inactive_days 60; "sadık / müdavim" ≈ min_visits 3; "yeni gelenler" ≈ 1 visit.
- Messages: Turkish, in the business's tone, personal, mention only discounts and prices that are in THIS plan,
  one emoji at most, under 350 characters. Do not promise anything the plan does not do.
- Template campaigns: a business-initiated WhatsApp message is only delivered through an approved
  template — call list_campaign_templates, pick the one matching the intent (reengagement for win-back,
  thankyou for loyal customers, welcome for newcomers) and fill ONLY its variables, never {{1}} (the
  gateway inserts each customer's name). Put the offer into `advantage`/`discount` exactly as planned
  (e.g. "hafta sonu tüm fularlarda %15 indirim"); `code` is a short uppercase code like `FULAR15`.
  Free-text `message` works in mock demos only.
- The campaign visual prompt is in English and must describe the actual items (detected or described).
- Social posts: for Instagram/Facebook requests use publish_social_post with a Turkish
  caption (≤2200 chars, ≤15 hashtags, no URLs/phones, prices only from this plan), platforms,
  optional publish_at (ISO, 5 min–30 days out), an English visual_prompt naming the items,
  and item_refs (photo temp_ids give real crops).
- Order actions: add_catalog_item → create_discount → generate_visual → send_campaign;
  publish_social_post after the items it features; appointment actions
  may come anywhere.
- Do NOT answer in plain text; always finish with propose_actions or reply_only.
- If the instruction is unclear or unsafe, still call propose_actions with the safe subset you can do and
  explain what you skipped in summary_tr — or reply_only with one precise question.
- `recent_conversation` holds the last messages of this chat. Use it to resolve short replies such as
  'olur', 'evet o saat', 'ikincisi', '17:30 olsun'. The latest owner message wins. An approval still
  always goes through propose_actions and the card; never treat 'olur' as an approval by itself.
- Bulk prices, loyalty, ads: for "tüm X'e zam/indirim" ALWAYS call preview_bulk_price first (mode
  percent/amount, negative lowers), then apply_bulk_price with that preview_id — never invent prices. For
  loyalty points resolve the customer with find_customer, then adjust_loyalty (points ≠ 0). For ads read
  get_ads_performance (+ get_ads_suggestions); pure questions ("reklamlar nasıl?") get reply_only with
  numbers ONLY from the tools, changes (pause/resume/budget) go through ads_action with a tool-seen id.
"""

_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "name": "get_store_profile",
     "description": "Business name, city, sector, tone of voice, channels, staff, business hours, today's date, counts.",
     "parameters": _NO_ARGS},
    {"type": "function", "name": "list_catalog",
     "description": "Existing catalog items (products and services) with ids, prices, durations, current sale prices.",
     "parameters": {"type": "object", "properties": {
         "item_type": {"type": "string", "enum": ["product", "service"], "description": "filter; omit for all"}},
         "required": [], "additionalProperties": False}},
    {"type": "function", "name": "find_customers",
     "description": "Build a customer segment from the CRM (only customers with marketing consent). "
                    "Returns a segment_id to use in send_campaign plus a count; never phone numbers.",
     "parameters": {"type": "object", "properties": {
         "inactive_days": {"type": "integer", "description": "last visit at least this many days ago (e.g. 60 for 'uzun süredir gelmeyenler')"},
         "min_visits": {"type": "integer", "description": "minimum past visits/purchases (default 1; 3+ for 'sadık müşteriler')"},
         "tags": {"type": "array", "items": {"type": "string"}, "description": "interest tags, e.g. ['çanta'] or ['boya']"}},
         "required": [], "additionalProperties": False}},
    {"type": "function", "name": "find_customer",
     "description": "Resolve ONE customer by (partial) name to an id, for appointments. Returns possibly several matches.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                    "required": ["name"], "additionalProperties": False}},
    {"type": "function", "name": "get_schedule",
     "description": "Appointments and free slots per staff member for one day (services businesses).",
     "parameters": {"type": "object", "properties": {
         "day": {"type": "string", "description": "ISO date, e.g. 2026-10-28"}},
         "required": ["day"], "additionalProperties": False}},
    {"type": "function", "name": "find_appointments",
     "description": "Existing appointments by customer name and/or day; returns appointment ids for reschedule/cancel.",
     "parameters": {"type": "object", "properties": {
         "customer_name": {"type": "string"}, "day": {"type": "string", "description": "ISO date"}},
         "required": [], "additionalProperties": False}},
    {"type": "function", "name": "get_daily_summary",
     "description": "What happened on one day: appointments, revenue, new customers/leads, unanswered messages, "
                    "campaigns, catalog changes. Use for 'bugün ne oldu?'.",
     "parameters": {"type": "object", "properties": {"day": {"type": "string", "description": "ISO date"}},
                    "required": ["day"], "additionalProperties": False}},
    {"type": "function", "name": "preview_bulk_price",
     "description": "Preview a bulk price change (percent or TRY amount, negative lowers prices) over a "
                    "category, an item type, or explicit item ids. Returns a preview_id plus before/after "
                    "rows. Always preview before apply_bulk_price.",
     "parameters": {"type": "object", "properties": {
         "item_type": {"type": "string", "enum": ["product", "service"]},
         "category": {"type": "string", "description": "e.g. 'çanta', 'elbise', 'boya'; omit to match all"},
         "item_ids": {"type": "array", "items": {"type": "string"}},
         "mode": {"type": "string", "enum": ["percent", "amount"]},
         "value": {"type": "number", "description": "percent (e.g. 10) or TRY amount (e.g. 50), "
                                                    "may be negative"}},
         "required": ["mode", "value"], "additionalProperties": False}},
    {"type": "function", "name": "get_ads_performance",
     "description": "Ad campaigns with spend, impressions, clicks, results and cost per result, plus totals.",
     "parameters": {"type": "object", "properties": {
         "days": {"type": "integer", "description": "lookback window (default 7)"}},
         "required": [], "additionalProperties": False}},
    {"type": "function", "name": "get_ads_suggestions",
     "description": "Rule-based suggestions on the ad campaigns (pause losers, shift budget to winners).",
     "parameters": _NO_ARGS},
    {"type": "function", "name": "list_campaign_templates",
     "description": "Approved WhatsApp templates for business-initiated campaigns: name, body with "
                    "{{n}} placeholders, variables. {{1}} is always the customer name (filled per "
                    "recipient); the plan only supplies the remaining variables.",
     "parameters": _NO_ARGS},
    {"type": "function", "name": "propose_actions",
     "description": "Submit the final plan for human approval. Call exactly once when done.",
     "parameters": PROPOSAL_SCHEMA},
    {"type": "function", "name": "reply_only",
     "description": "Finish WITHOUT any action: answer the owner's question from the data you read. Turkish.",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"], "additionalProperties": False}},
]


@dataclass
class AgentRun:
    proposal: dict[str, Any] | None = None
    answer: str | None = None
    segments: set[str] = field(default_factory=set)
    appointments: set[str] = field(default_factory=set)
    customers: set[str] = field(default_factory=set)
    previews: dict[str, dict[str, Any]] = field(default_factory=dict)
    campaigns: set[str] = field(default_factory=set)
    templates: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})


def _run_tool(name: str, args: dict[str, Any], backend: Backend, run: AgentRun) -> dict[str, Any]:
    if name == "get_store_profile":
        return backend.store_profile()
    if name == "list_catalog":
        return {"items": backend.list_catalog(args.get("item_type"))}
    if name == "find_customers":
        res = backend.find_customers(
            inactive_days=args.get("inactive_days"),
            min_visits=int(args.get("min_visits") or 1),
            tags=args.get("tags"),
        )
        run.segments.add(res["segment_id"])
        return res
    if name == "find_customer":
        matches = backend.find_customer(args.get("name") or "")
        run.customers.update(m["id"] for m in matches)
        return {"matches": matches}
    if name == "get_schedule":
        return backend.get_schedule(args["day"])
    if name == "find_appointments":
        found = backend.find_appointments(args.get("customer_name"), args.get("day"))
        run.appointments.update(a["id"] for a in found)
        return {"appointments": found}
    if name == "get_daily_summary":
        return backend.daily_summary(args["day"])
    if name == "preview_bulk_price":
        res = backend.preview_bulk_price(
            item_type=args.get("item_type"),
            category=args.get("category"),
            item_ids=args.get("item_ids"),
            mode=args.get("mode"),
            value=args.get("value"),
        )
        run.previews[res["preview_id"]] = res
        return res
    if name == "get_ads_performance":
        res = backend.ads_performance(args.get("days", 7))
        run.campaigns.update(c["id"] for c in res.get("campaigns", []))
        return res
    if name == "get_ads_suggestions":
        return backend.ads_suggestions()
    if name == "list_campaign_templates":
        templates = backend.campaign_templates()
        run.templates.update({t["name"]: t for t in templates})
        return {"templates": templates}
    raise KeyError(name)


def plan(ctx: MediaContext, backend: Backend, max_revisions: int = 3) -> AgentRun:
    """Phase 1: run the tool loop until a valid proposal (or a plain answer) is produced."""
    run = AgentRun()
    client = meta.client()
    known_temp = {p.temp_id for p in ctx.products}
    catalog = {i["id"]: i for i in backend.list_catalog()}

    user_msg = (
        "Here is what the owner sent, already processed:\n"
        + json.dumps(ctx.to_prompt_json(), ensure_ascii=False, indent=2)
        + "\n\nInspect the business with the tools, then call propose_actions or reply_only."
    )
    response = client.responses.create(
        model=settings.orchestrator_model,
        instructions=SYSTEM_PROMPT,
        reasoning={"effort": settings.reasoning_effort},
        input=user_msg,
        tools=TOOLS,
    )
    revisions = 0

    while run.turns < settings.max_agent_turns:
        run.turns += 1
        if response.usage:
            run.usage["input_tokens"] += response.usage.input_tokens
            run.usage["output_tokens"] += response.usage.output_tokens

        calls = [o for o in response.output if o.type == "function_call"]
        if not calls:
            # Model answered in prose. Nudge it back to the protocol (length only, never the text).
            log.warning("no tool call; nudging. prose_chars=%d", len(response.output_text or ""))
            response = client.responses.create(
                model=settings.orchestrator_model,
                previous_response_id=response.id,
                input="Please finish by calling propose_actions (plan) or reply_only (answer). No prose.",
                tools=TOOLS,
            )
            continue

        outputs: list[dict[str, Any]] = []
        for call in calls:
            args = json.loads(call.arguments or "{}")
            if call.name == "reply_only":
                text = (args.get("text") or "").strip()
                run.tool_log.append({"tool": "reply_only", "chars": len(text)})
                if text:
                    run.answer = text
                    return run
                result = {"accepted": False, "errors": ["text is empty"]}
            elif call.name == "propose_actions":
                errors = validate_proposal(args, known_temp, catalog, run.segments,
                                           run.appointments, run.customers,
                                           run.previews, run.campaigns, run.templates,
                                           requires_template=isinstance(backend, KeddBackend))
                run.tool_log.append({"tool": "propose_actions", "errors": errors,
                                     **({"rejected_proposal": args} if errors else {})})
                if not errors:
                    run.proposal = args
                    return run
                revisions += 1
                codes = sorted({e.split(":")[0] if e.startswith("actions[") else "plan" for e in errors})
                types = sorted({a.get("type", "?") for a in args.get("actions", [])})
                log.warning("proposal rejected (%d/%d): %d errors codes=%s types=%s",
                            revisions, max_revisions, len(errors), codes, types)
                if revisions > max_revisions:
                    raise RuntimeError(f"proposal still invalid after {max_revisions} revisions "
                                       f"({len(errors)} errors)")   # error texts quote model values
                result = {"accepted": False, "errors": errors,
                          "hint": "Fix every error and call propose_actions again with the COMPLETE plan "
                                  "(all actions, in order). Keep everything that was already valid.",
                          "known_segment_ids": sorted(run.segments),
                          "known_appointment_ids": sorted(run.appointments),
                          "known_customer_ids": sorted(run.customers),
                          "known_preview_ids": sorted(run.previews),
                          "known_campaign_ids": sorted(run.campaigns),
                          "known_template_names": sorted(run.templates)}
            else:
                try:
                    result = _run_tool(call.name, args, backend, run)
                except Exception as e:  # tool errors go back to the model, not up the stack
                    result = {"error": f"{type(e).__name__}: {e}"}
                run.tool_log.append({"tool": call.name, "args": args, "result": result})
            outputs.append({"type": "function_call_output", "call_id": call.call_id,
                            "output": json.dumps(result, ensure_ascii=False)})

        response = client.responses.create(
            model=settings.orchestrator_model,
            previous_response_id=response.id,
            input=outputs,
            tools=TOOLS,
        )

    raise RuntimeError("agent did not produce a proposal within max_agent_turns")
