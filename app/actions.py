"""Action schema + validation.

The model PROPOSES actions as data. Nothing is executed until (a) the proposal passes
these checks and (b) a human approves it. Business rules live here, in code.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .config import settings

ACTION_TYPES = {"add_catalog_item", "create_discount", "generate_visual", "send_campaign"}

# JSON schema handed to the model (Responses API flat tool format)
PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary_tr": {"type": "string",
                       "description": "2-3 short Turkish sentences the merchant will read on WhatsApp before approving."},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": sorted(ACTION_TYPES)},
                    # add_catalog_item
                    "item_type": {"type": "string", "enum": ["product", "service"]},
                    "temp_id": {"type": "string",
                                "description": "detected product temp_id when the item comes from a photo; omit for items described in words"},
                    "ref": {"type": "string",
                            "description": "your own short handle for a text-only item (e.g. 'new1'), reusable in later actions"},
                    "name": {"type": "string"},
                    "price": {"type": "number", "description": "TRY, exactly as the owner said"},
                    "category": {"type": "string"},
                    "description": {"type": "string", "description": "catalog text, Turkish"},
                    "duration_minutes": {"type": "integer", "description": "services only"},
                    # create_discount
                    "percent": {"type": "integer"},
                    "item_refs": {"type": "array", "items": {"type": "string"},
                                  "description": "temp_ids / refs of new items and/or existing catalog item ids"},
                    "starts": {"type": "string", "description": "ISO date"},
                    "ends": {"type": "string", "description": "ISO date"},
                    # generate_visual
                    "visual_prompt": {"type": "string",
                                      "description": "English prompt for the campaign image, mention the items"},
                    # send_campaign
                    "segment_id": {"type": "string", "description": "from find_customers"},
                    "message": {"type": "string", "description": "WhatsApp message, Turkish, personal, <= 350 chars"},
                    "attach_visual": {"type": "boolean"},
                },
                "required": ["type"],
            },
        },
    },
    "required": ["summary_tr", "actions"],
}


def _iso(s: str) -> date:
    return date.fromisoformat(s)


# "%15", "% 15", "yüzde 15"  /  "850 TL", "1.290 ₺", "850 lira"
_PCT_RE = re.compile(r"(?:%\s?(\d{1,3})|yüzde\s+(\d{1,3}))", re.I)
_PRICE_RE = re.compile(r"(\d{1,3}(?:[.\s]\d{3})*|\d+)\s?(?:TL|₺|lira)", re.I)


def _promised_numbers(message: str) -> tuple[set[int], set[float]]:
    pcts = {int(a or b) for a, b in _PCT_RE.findall(message)}
    prices = {float(p.replace(".", "").replace(" ", "")) for p in _PRICE_RE.findall(message)}
    return pcts, prices


def _coerce_numbers(a: dict[str, Any]) -> None:
    """Tool-call arguments sometimes arrive as strings ("20") or floats (20.0). Normalise in place
    so the validator and the executor see plain numbers."""
    for key, want_int in (("percent", True), ("duration_minutes", True), ("price", False)):
        v = a.get(key)
        if isinstance(v, str):
            v = v.strip().replace(",", ".")
            try:
                v = float(v)
            except ValueError:
                continue
        if isinstance(v, float) and want_int and v.is_integer():
            v = int(v)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            a[key] = v


# summary talks about messaging customers → the plan must contain a send_campaign
_CAMPAIGN_WORDS_RE = re.compile(r"kampanya|mesaj|haber ver|bildir|duyur", re.I)
# ...unless the summary says that no message is being sent ("mesaj göndermedim", "kampanya yapmadım")
_NEGATED_CAMPAIGN_RE = re.compile(
    r"(kampanya|mesaj|haber|bildirim|duyuru)[^.!\n]{0,60}"
    r"(göndermedim|göndermeyeceğim|gönderilmeyecek|gönderilmedi|atmadım|atmayacağım|yapmadım|yapmayacağım|"
    r"hazırlamadım|oluşturmadım|yok|atlıyorum|atladım)", re.I)


def validate_proposal(proposal: dict[str, Any], known_temp_ids: set[str],
                      catalog: dict[str, dict[str, Any]], known_segments: set[str]) -> list[str]:
    """Return a list of problems (empty = valid).

    `catalog` maps existing item id -> item dict (needs at least "price")."""
    errors: list[str] = []
    if not isinstance(proposal, dict):
        return ["proposal must be an object"]
    if not proposal.get("summary_tr"):
        errors.append("summary_tr is required")
    actions = proposal.get("actions")
    if not isinstance(actions, list) or not actions:
        return errors + ["actions must be a non-empty list"]

    new_refs: dict[str, float] = {}        # temp_id / ref -> price of items being added
    plan_pcts: set[int] = set()
    plan_prices: set[float] = set()
    has_visual = False
    for i, a in enumerate(actions):
        t = a.get("type")
        where = f"actions[{i}] ({t})"
        if t not in ACTION_TYPES:
            errors.append(f"{where}: unknown type"); continue
        _coerce_numbers(a)

        if t == "add_catalog_item":
            if a.get("item_type") not in ("product", "service"):
                errors.append(f"{where}: item_type must be 'product' or 'service'")
            tid, ref = a.get("temp_id"), a.get("ref")
            handle = tid or ref
            if tid and tid not in known_temp_ids:
                errors.append(f"{where}: temp_id must be one of {sorted(known_temp_ids)} (or omit it for a text-only item)")
            if not handle:
                errors.append(f"{where}: give temp_id (photo item) or ref (text item)")
            elif handle in new_refs or handle in catalog:
                errors.append(f"{where}: {handle} added twice / clashes with an existing id")
            price = a.get("price")
            if not isinstance(price, (int, float)) or price <= 0 or price > 500_000:
                errors.append(f"{where}: price must be a positive number in TRY")
            else:
                plan_prices.add(float(price))
            if not a.get("name"):
                errors.append(f"{where}: name is required")
            if a.get("item_type") == "service":
                dur = a.get("duration_minutes")
                if dur is not None and (not isinstance(dur, int) or not 5 <= dur <= 600):
                    errors.append(f"{where}: duration_minutes must be 5..600")
            if handle:
                new_refs[handle] = float(price) if isinstance(price, (int, float)) else 0.0

        elif t == "create_discount":
            pct = a.get("percent")
            if not isinstance(pct, int) or not (1 <= pct <= settings.max_discount_pct):
                errors.append(f"{where}: percent must be an integer 1..{settings.max_discount_pct}")
            else:
                plan_pcts.add(pct)
            refs = a.get("item_refs") or []
            bad = [r for r in refs if r not in new_refs and r not in catalog]
            if not refs:
                errors.append(f"{where}: item_refs is required")
            if bad:
                errors.append(f"{where}: unknown item refs {bad} (add_catalog_item must come first)")
            for r in refs:
                base = new_refs.get(r) or float(catalog.get(r, {}).get("price") or 0)
                if base and isinstance(pct, int):
                    plan_prices.update({base, round(base * (100 - pct) / 100, 2), float(round(base * (100 - pct) / 100))})
            try:
                s, e = _iso(a.get("starts", "")), _iso(a.get("ends", ""))
                if e < s:
                    errors.append(f"{where}: ends before starts")
                if (e - s).days > 60:
                    errors.append(f"{where}: discount longer than 60 days needs manual setup")
            except ValueError:
                errors.append(f"{where}: starts/ends must be ISO dates")

        elif t == "generate_visual":
            if not a.get("visual_prompt"):
                errors.append(f"{where}: visual_prompt is required")
            refs = a.get("item_refs") or []
            bad = [r for r in refs if r not in new_refs and r not in known_temp_ids and r not in catalog]
            if bad:
                errors.append(f"{where}: unknown item refs {bad}")
            has_visual = True

        elif t == "send_campaign":
            if a.get("segment_id") not in known_segments:
                errors.append(f"{where}: segment_id must come from a find_customers call "
                              f"(known: {sorted(known_segments)})")
            msg = a.get("message") or ""
            if not msg:
                errors.append(f"{where}: message is required")
            elif len(msg) > 350:
                errors.append(f"{where}: message too long ({len(msg)} > 350 chars)")
            if a.get("attach_visual") and not has_visual:
                errors.append(f"{where}: attach_visual=true but no generate_visual action before it")
            # The message must not promise what the plan does not do.
            pcts, prices = _promised_numbers(msg)
            for p in sorted(pcts - plan_pcts):
                errors.append(f"{where}: message mentions %{p} but no create_discount in this plan has percent={p}")
            for p in sorted(prices - plan_prices):
                errors.append(f"{where}: message mentions {p:g} TL but no action in this plan sets that price "
                              f"(add the item or drop the number)")

    # Plan/summary consistency: an audience was built and the summary promises a message, but nothing is sent.
    types = {a.get("type") for a in actions}
    summary = proposal.get("summary_tr") or ""
    if (known_segments and "send_campaign" not in types and _CAMPAIGN_WORDS_RE.search(summary)
            and not _NEGATED_CAMPAIGN_RE.search(summary)):
        errors.append("summary_tr talks about a campaign/message and a customer segment was built, but there is "
                      "no send_campaign action — add it (with the segment_id) or rewrite summary_tr to say "
                      "clearly that no message will be sent")
    return errors
