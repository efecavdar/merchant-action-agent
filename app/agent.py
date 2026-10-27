"""Orchestrator: Muse Spark plans with tools, then hands a proposal to a human.

Design (deliberately conservative — this agent sends messages to real customers in production):

  Phase 1  PLAN     Muse Spark reads the MediaContext, calls READ-ONLY tools
                    (business profile, catalog, customer segments, schedule) and finishes by
                    calling `propose_actions` with a structured plan.
  Gate     VALIDATE Code validates the plan (schema + business rules). Problems are sent
                    back to the model, which revises. Max 2 revisions.
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
from .tools.backend import Backend

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are "Çırak", the operations assistant of a small Turkish business (a boutique, a hair salon,
a café...). The owner sends you an instruction — usually a WhatsApp voice note, sometimes with product photos that
have already been detected and described for you. Your job is to turn the instruction into a concrete, safe plan.

Rules:
- Always start with get_store_profile (sector, today's date, tone) and list_catalog. Never plan blind.
- Catalog items are products OR services (a service has a duration). You can add an item from a detected photo
  (temp_id) or from the owner's words alone (ref). "Keratin bakımı ekle, 90 dakika, 2500 lira" is a complete
  service; "yeni ipek fular koleksiyonu, tanesi 850" is a product even without a photo.
- Prices: use exactly what the owner said. If one price is given for several items, apply it to each. Never guess
  a price that was not stated; if it is missing, leave the item out and say so in summary_tr. Never apply a
  discount to unrelated existing items just because a new item could not be created.
- Discounts: integers only, dates as ISO (YYYY-MM-DD). "hafta sonu" = the coming Saturday–Sunday based on
  today's date. "yarın" = tomorrow. Use get_schedule when the owner talks about empty hours / appointments.
- Audience: call find_customers whenever the owner wants to notify customers; never invent a segment id.
  "uzun süredir gelmeyenler" ≈ inactive_days 60; "sadık / müdavim" ≈ min_visits 3; "yeni gelenler" ≈ 1 visit.
- Messages: Turkish, in the business's tone, personal, mention only discounts and prices that are in THIS plan,
  one emoji at most, under 350 characters. Do not promise anything the plan does not do.
- The campaign visual prompt is in English and must describe the actual items (detected or described).
- Order actions: add_catalog_item → create_discount → generate_visual → send_campaign.
- When the plan is ready, call propose_actions exactly once. Do NOT answer in plain text.
- If the instruction is unclear or unsafe, still call propose_actions with the safe subset you can do and
  explain what you skipped in summary_tr.
"""

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "name": "get_store_profile",
     "description": "Business name, city, sector, tone of voice, channels, staff, business hours, today's date, counts.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
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
    {"type": "function", "name": "get_schedule",
     "description": "Appointments and free slots per staff member for one day (services businesses).",
     "parameters": {"type": "object", "properties": {
         "day": {"type": "string", "description": "ISO date, e.g. 2026-10-28"}},
         "required": ["day"], "additionalProperties": False}},
    {"type": "function", "name": "propose_actions",
     "description": "Submit the final plan for human approval. Call exactly once when done.",
     "parameters": PROPOSAL_SCHEMA},
]


@dataclass
class AgentRun:
    proposal: dict[str, Any] | None = None
    segments: set[str] = field(default_factory=set)
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
    if name == "get_schedule":
        return backend.get_schedule(args["day"])
    raise KeyError(name)


def plan(ctx: MediaContext, backend: Backend, max_revisions: int = 2) -> AgentRun:
    """Phase 1: run the tool loop until a valid proposal is produced."""
    run = AgentRun()
    client = meta.client()
    known_temp = {p.temp_id for p in ctx.products}
    catalog = {i["id"]: i for i in backend.list_catalog()}

    user_msg = (
        "Here is what the owner sent, already processed:\n"
        + json.dumps(ctx.to_prompt_json(), ensure_ascii=False, indent=2)
        + "\n\nInspect the business with the tools, then call propose_actions."
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
            # Model answered in prose. Nudge it back to the protocol once.
            log.warning("no tool call; nudging. text=%r", (response.output_text or "")[:200])
            response = client.responses.create(
                model=settings.orchestrator_model,
                previous_response_id=response.id,
                input="Please finish by calling propose_actions with your plan (no prose).",
                tools=TOOLS,
            )
            continue

        outputs: list[dict[str, Any]] = []
        for call in calls:
            args = json.loads(call.arguments or "{}")
            if call.name == "propose_actions":
                errors = validate_proposal(args, known_temp, catalog, run.segments)
                run.tool_log.append({"tool": "propose_actions", "errors": errors,
                                     **({"rejected_proposal": args} if errors else {})})
                if not errors:
                    run.proposal = args
                    return run
                revisions += 1
                if revisions > max_revisions:
                    raise RuntimeError(f"proposal still invalid after {max_revisions} revisions: {errors}")
                result = {"accepted": False, "errors": errors,
                          "hint": "Fix every error and call propose_actions again."}
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
