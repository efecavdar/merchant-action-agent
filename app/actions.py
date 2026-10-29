"""Action schema + validation.

The model PROPOSES actions as data. Nothing is executed until (a) the proposal passes
these checks and (b) a human approves it. Business rules live here, in code.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from .config import settings
from .tools.backend import render_campaign_template

MAX_ACTIONS_PER_PLAN = 12
MAX_NAME_CHARS = 60
MAX_DESCRIPTION_CHARS = 500
MAX_CATEGORY_CHARS = 40

ACTION_TYPES = {"add_catalog_item", "create_discount", "generate_visual", "send_campaign",
                "create_appointment", "reschedule_appointment", "cancel_appointment",
                "complete_appointment", "create_quote_draft", "teach_chatbot",
                "apply_bulk_price", "adjust_loyalty", "ads_action", "publish_social_post",
                "create_customer", "update_customer", "create_lead", "set_lead_stage",
                "add_lead_note", "create_task", "complete_task"}
SOCIAL_PLATFORMS = ("instagram", "facebook")
MAX_CAPTION_CHARS = 2200
MAX_HASHTAGS = 15
APPOINTMENT_TYPES = {"create_appointment", "reschedule_appointment", "cancel_appointment",
                     "complete_appointment"}
ADS_ACTIONS = {"pause", "resume", "increase_budget", "decrease_budget"}
CRM_TYPES = {"create_customer", "update_customer", "create_lead", "set_lead_stage",
             "add_lead_note", "create_task", "complete_task"}
LEAD_STAGES = ("new", "contacted", "qualified", "proposal", "won", "lost")
MAX_NOTE_CHARS = 500
MAX_TAGS, MAX_TAG_CHARS, MAX_TITLE_CHARS = 10, 30, 120
APPT_METHODS = ("cash", "card", "transfer", "other")
MAX_APPT_AMOUNT = 100_000
MAX_QUESTION_CHARS = 200
MAX_ANSWER_CHARS = 1000
MAX_VALID_DAYS = 60
MIN_QUOTE_PRICE_RATIO = 0.5

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
                            "description": "your own short handle for a text-only item or a new "
                                           "customer/lead/task "
                                           "(e.g. 'new1'), reusable in later actions"},
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
                    "message": {"type": "string",
                                "description": "WhatsApp message, Turkish, personal, <= 350 chars "
                                               "(mock free-text only; omit when template is set)"},
                    "template": {"type": "string",
                                 "description": "approved template name from list_campaign_templates "
                                                "(required for real WhatsApp sends)"},
                    "variables": {"type": "object",
                                  "description": "template variables by number, e.g. "
                                                 "{\"2\": \"OZEL20\", \"3\": \"hafta sonu %20\"}; "
                                                 "never \"1\""},
                    "attach_visual": {"type": "boolean"},
                    # publish_social_post
                    "caption": {"type": "string",
                                "description": "post caption, Turkish, <= 2200 chars, <= 15 "
                                               "hashtags, no URLs or phone numbers"},
                    "platforms": {"type": "array", "items": {"type": "string"},
                                  "description": "subset of instagram, facebook"},
                    "publish_at": {"type": "string",
                                   "description": "ISO datetime to schedule (optional; "
                                                  "5 minutes to 30 days out, else publishes now)"},
                    # appointments
                    "customer_id": {"type": "string",
                                    "description": "from find_customer, or a ref you created "
                                                   "earlier in this plan"},
                    "lead_id": {"type": "string",
                                "description": "from find_leads, or a ref you created earlier in this plan"},
                    "task_id": {"type": "string",
                                "description": "from list_tasks, or a ref you created earlier in this plan"},
                    "phone": {"type": "string",
                              "description": "optional contact phone (E.164, e.g. +905XXXXXXXXX)"},
                    "email": {"type": "string", "description": "optional contact e-mail"},
                    "tags": {"type": "array", "items": {"type": "string"},
                             "description": "at most 10 tags, 30 chars each, e.g. ['kafe', 'vip']"},
                    "note": {"type": "string",
                             "description": "owner note, at most 500 chars, no URLs, e-mails or "
                                            "phone numbers"},
                    "add_tags": {"type": "array", "items": {"type": "string"},
                                 "description": "tags to add (same limits as tags)"},
                    "remove_tags": {"type": "array", "items": {"type": "string"},
                                    "description": "tags to remove"},
                    "estimated_value": {"type": "number",
                                        "description": "estimated deal value in TRY (0 or more)"},
                    "stage": {"type": "string", "enum": list(LEAD_STAGES)},
                    "title": {"type": "string", "description": "task title, at most 120 chars"},
                    "due_date": {"type": "string",
                                 "description": "ISO date (YYYY-MM-DD) or datetime, today or later, "
                                                "within a year"},
                    "item_ref": {"type": "string", "description": "service id from list_catalog (create_appointment)"},
                    "staff": {"type": "string", "description": "staff member name (create/reschedule)"},
                    "appointment_id": {"type": "string",
                                     "description": "from find_appointments or get_schedule "
                                                  "(reschedule/cancel/complete)"},
                    "reason": {"type": "string", "description": "cancel (optional) / loyalty reason"},
                    # complete_appointment
                    "amount": {"type": "number",
                               "description": "TRY taken for the appointment; omit to charge the "
                                            "service's catalog price"},
                    "method": {"type": "string", "enum": list(APPT_METHODS)},
                    # create_quote_draft
                    "items": {"type": "array", "items": {"type": "object"},
                              "description": "quote rows: {catalog_item_id from list_catalog, "
                                           "qty 1-100, unit_price? in TRY}"},
                    "catalog_item_id": {"type": "string",
                                        "description": "existing catalog id from list_catalog"},
                    "qty": {"type": "integer", "description": "quote row quantity, 1-100"},
                    "unit_price": {"type": "number",
                                   "description": "TRY per unit; at least half the catalog price, "
                                                "omit for the catalog price"},
                    "valid_days": {"type": "integer",
                                   "description": "how long the quote stays valid, 1-60 days"},
                    # teach_chatbot
                    "question": {"type": "string",
                                 "description": "what the chatbot should answer, at most 200 chars, "
                                              "no contact details"},
                    "answer": {"type": "string",
                               "description": "the chatbot's answer, at most 1000 chars, no contact "
                                            "details; prices only from this run's catalog"},
                    # bulk price / loyalty / ads
                    "preview_id": {"type": "string",
                                   "description": "from preview_bulk_price (apply_bulk_price)"},
                    "points": {"type": "integer",
                               "description": "points to add (negative subtracts), 1..1000 by magnitude"},
                    "campaign_id": {"type": "string", "description": "from get_ads_performance (ads_action)"},
                    "action": {"type": "string", "enum": sorted(ADS_ACTIONS)},
                    "delta_pct": {"type": "integer",
                                  "description": "budget change 1..20, for increase/decrease_budget"},
                    "label": {"type": "string",
                              "description": "one Turkish line the owner will see on the approval card, e.g. "
                                             "'Elif K. — Demo Görüşmesi, Efe, 31 Ekim Cuma 15:00'"},
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


_URL_RE = re.compile(r"https?://|www\.|\b(?:wa|t)\.me\b|\b[\w-]+\.(?:com|net|org|me|ly|link|io|co|shop|store|app|tr)\b",
                     re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_INTL_RE = re.compile(r"\+\d[\d\s().-]{7,}\d")
_PHONE_TR_RE = re.compile(r"(?<!\d)0?5\d{2}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}(?!\d)")
_PHONE_DIGITS_RE = re.compile(r"(?<!\d)(?:90|0)?[2-5]\d{9}(?!\d)")   # after () . - and spaces are removed
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.I)
_IBAN_TR_RE = re.compile(r"TR\d{24}", re.I)   # Turkish IBAN once spaces are removed
# Offer words that promise a deal: only a plan with a discount action may use them.
# "hediyelik eşya" and "ücretsiz kargo" are not deals on the items.
_OFFER_WORDS_RE = re.compile(r"\bbedava|\bücretsiz\b(?!\s+kargo)|\byarı\s+fiyat|\b1\s+alana\s+1\b|\bhediye(?!lik)",
                             re.I)


def _contains_phone(text: str) -> bool:
    return bool(_PHONE_INTL_RE.search(text) or _PHONE_TR_RE.search(text)
                or _PHONE_DIGITS_RE.search(re.sub(r"[()\s.-]", "", text)))


def _check_no_contacts(where: str, what: str, text: str, errors: list[str]) -> None:
    """Customer-facing text must not smuggle out contact details or payment rails."""
    if _URL_RE.search(text):
        errors.append(f"{where}: {what} must not contain a URL")
    if _EMAIL_RE.search(text):
        errors.append(f"{where}: {what} must not contain an e-mail address")
    if _contains_phone(text):
        errors.append(f"{where}: {what} must not contain a phone number")
    if _IBAN_RE.search(text) or _IBAN_TR_RE.search(re.sub(r"\s+", "", text)):
        errors.append(f"{where}: {what} must not contain an IBAN")


def _check_crm_name(where: str, value: Any, errors: list[str], required: bool = True) -> None:
    if value is None:
        if required:
            errors.append(f"{where}: name is required")
        return
    if not isinstance(value, str) or not 2 <= len(value.strip()) <= MAX_NAME_CHARS:
        errors.append(f"{where}: name must be 2-{MAX_NAME_CHARS} chars")


def _check_crm_note(where: str, value: Any, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        errors.append(f"{where}: note must be a string")
        return
    if len(value) > MAX_NOTE_CHARS:
        errors.append(f"{where}: note too long ({len(value)} > {MAX_NOTE_CHARS} chars)")
    _check_no_contacts(where, "note", value, errors)


def _check_crm_tags(where: str, field: str, value: Any, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or any(not isinstance(t, str) for t in value):
        errors.append(f"{where}: {field} must be a list of strings")
        return
    if len(value) > MAX_TAGS:
        errors.append(f"{where}: {field} holds at most {MAX_TAGS} tags")
    for t in value:
        if len(t) > MAX_TAG_CHARS:
            errors.append(f"{where}: {field} tags must be {MAX_TAG_CHARS} chars or less ({t!r})")
        _check_no_contacts(where, field, t, errors)


def _check_promised_numbers(where: str, text: str, plan_pcts: set[int], plan_prices: set[float],
                            errors: list[str]) -> None:
    """The customer text must not promise what the plan does not do."""
    pcts, prices = _promised_numbers(text)
    for p in sorted(pcts - plan_pcts):
        errors.append(f"{where}: message mentions %{p} but no create_discount in this plan has percent={p}")
    for p in sorted(prices - plan_prices):
        errors.append(f"{where}: message mentions {p:g} TL but no action in this plan sets that price "
                      f"(add the item or drop the number)")


def _check_answer_prices(where: str, answer: Any, catalog: dict[str, dict[str, Any]],
                         plan_prices: set[float], errors: list[str]) -> None:
    """A chatbot answer must not teach prices the run never saw (catalog or this plan)."""
    if not isinstance(answer, str):
        return
    known = {float(v["price"]) for v in catalog.values() if _is_number(v.get("price"))}
    for p in sorted(_promised_numbers(answer)[1] - (known | plan_prices)):
        errors.append(f"{where}: answer mentions {p:g} TL but no catalog item in this run has "
                      f"that price (the chatbot must not learn wrong prices)")


def _check_template_variables(a: dict[str, Any], tmpl: dict[str, Any], where: str,
                              errors: list[str]) -> None:
    variables = a.get("variables")
    if variables is None:
        variables = {}
    if not isinstance(variables, dict):
        errors.append(f"{where}: variables must be an object like {{\"2\": \"OZEL20\"}}")
        return
    required = {str(v.get("number")) for v in tmpl.get("variables") or [] if str(v.get("number")) != "1"}
    provided = {str(k) for k in variables}
    if "1" in provided:
        errors.append(f"{where}: variables must not include '1' (the gateway fills the customer name)")
    missing = sorted(required - provided)
    if missing:
        errors.append(f"{where}: template '{a['template']}' needs variables {missing} "
                      f"(exactly {sorted(required)} besides '1')")
    extra = sorted(provided - required - {"1"})
    if extra:
        errors.append(f"{where}: template '{a['template']}' has no variables {extra} "
                      f"(needs exactly {sorted(required)} besides '1')")
    for k, v in variables.items():
        if not isinstance(v, str):
            errors.append(f"{where}: variables[{k!r}] must be a string")
            continue
        if len(v) > 60:
            errors.append(f"{where}: variables[{k!r}] too long ({len(v)} > 60 chars)")
        if "\n" in v or "\r" in v:
            errors.append(f"{where}: variables[{k!r}] must be a single line (no newline)")
        _check_no_contacts(where, f"variables[{k!r}]", v, errors)


def _is_number(v: Any) -> bool:
    """A real number for money math: int/float, never a bool."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _coerce_number(v: Any, want_int: bool) -> Any:
    """One sloppy tool-call number ("20", 20.0) → a plain number; anything else passes through
    untouched so the caller — never this helper — reports it."""
    if isinstance(v, str):
        v = v.strip().replace(",", ".")
        try:
            v = float(v)
        except ValueError:
            return v
    if isinstance(v, float) and want_int and v.is_integer():
        v = int(v)
    return v


def _coerce_numbers(a: dict[str, Any]) -> None:
    """Tool-call arguments sometimes arrive as strings ("20") or floats (20.0). Normalise in place
    so the validator and the executor see plain numbers."""
    for key, want_int in (("percent", True), ("duration_minutes", True), ("price", False),
                          ("points", True), ("delta_pct", True), ("estimated_value", False),
                          ("amount", False), ("unit_price", False), ("qty", True),
                          ("valid_days", True)):
        if key in a:
            v = _coerce_number(a[key], want_int)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                a[key] = v


# summary talks about messaging customers → the plan must contain a send_campaign
_CAMPAIGN_WORDS_RE = re.compile(r"kampanya|mesaj|haber ver|bildir|duyur", re.I)
# ...unless the summary says that no message is being sent ("mesaj göndermedim", "kampanya yapmadım")
_NEGATED_CAMPAIGN_RE = re.compile(
    r"(kampanya|mesaj|haber|bildirim|duyuru)[^.!\n]{0,60}"
    r"(göndermedim|göndermeyeceğim|gönderilmeyecek|gönderilmedi|atmadım|atmayacağım|yapmadım|yapmayacağım|"
    r"hazırlamadım|oluşturmadım|yok|atlıyorum|atladım)", re.I)


def _iso_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        raise ValueError("naive local datetime expected")
    return dt


def validate_proposal(proposal: dict[str, Any], known_temp_ids: set[str],
                      catalog: dict[str, dict[str, Any]], known_segments: set[str],
                      known_appointments: set[str] | None = None,
                      known_customers: set[str] | None = None,
                      known_previews: dict[str, dict[str, Any]] | None = None,
                      known_campaigns: set[str] | None = None,
                      known_templates: dict[str, dict[str, Any]] | None = None,
                      requires_template: bool | None = None,
                      known_leads: set[str] | None = None,
                      known_tasks: set[str] | None = None,
                      appointment_services: dict[str, str] | None = None) -> list[str]:
    """Return a list of problems (empty = valid).

    `catalog` maps existing item id -> item dict (needs at least "price", "type").
    Ids for appointments / customers / campaigns / previews / leads / tasks must come from
    a tool call in the same run (like segments); `known_previews` maps preview_id -> preview
    result, `known_templates` maps template name -> template (from list_campaign_templates).
    `requires_template` reflects the session's backend (True on KEDD: templates only);
    None falls back to the global BACKEND setting (older callers).
    `appointment_services` maps appointment id -> service name (from find_appointments or
    get_schedule); a completion without an amount is charged the service's catalog price."""
    known_appointments = known_appointments or set()
    known_customers = known_customers or set()
    known_previews = known_previews or {}
    known_campaigns = known_campaigns or set()
    known_templates = known_templates or {}
    known_leads = known_leads or set()
    known_tasks = known_tasks or set()
    appointment_services = appointment_services or {}
    needs_template = settings.backend == "kedd" if requires_template is None else requires_template
    errors: list[str] = []
    if not isinstance(proposal, dict):
        return ["proposal must be an object"]
    if not proposal.get("summary_tr"):
        errors.append("summary_tr is required")
    actions = proposal.get("actions")
    if not isinstance(actions, list) or not actions:
        return errors + ["actions must be a non-empty list"]
    if len(actions) > MAX_ACTIONS_PER_PLAN:
        errors.append(f"actions must be at most {MAX_ACTIONS_PER_PLAN} per plan (got {len(actions)})")

    new_refs: dict[str, float] = {}        # temp_id / ref -> price of items being added
    crm_refs: dict[str, str] = {}          # ref -> "customer" | "lead" | "task", created earlier
    plan_pcts: set[int] = set()
    plan_prices: set[float] = set()
    offer_texts: list[tuple[str, str]] = []  # (where, text) using offer words
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
            elif isinstance(a["name"], str):
                if len(a["name"]) > MAX_NAME_CHARS:
                    errors.append(f"{where}: name too long ({len(a['name'])} > {MAX_NAME_CHARS} chars)")
                _check_no_contacts(where, "name", a["name"], errors)
            if isinstance(a.get("description"), str):
                if len(a["description"]) > MAX_DESCRIPTION_CHARS:
                    errors.append(f"{where}: description too long "
                                  f"({len(a['description'])} > {MAX_DESCRIPTION_CHARS} chars)")
                _check_no_contacts(where, "description", a["description"], errors)
            if isinstance(a.get("category"), str) and len(a["category"]) > MAX_CATEGORY_CHARS:
                errors.append(f"{where}: category too long "
                              f"({len(a['category'])} > {MAX_CATEGORY_CHARS} chars)")
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
            if isinstance(refs, list):
                refs = list(dict.fromkeys(refs))  # de-duplicated, order kept
                a["item_refs"] = refs
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
                if s < date.today():
                    errors.append(f"{where}: starts is in the past (starts must be today or later)")
            except ValueError:
                errors.append(f"{where}: starts/ends must be ISO dates")

        elif t == "generate_visual":
            if not a.get("visual_prompt"):
                errors.append(f"{where}: visual_prompt is required")
            refs = a.get("item_refs") or []
            bad = [r for r in refs if r not in new_refs and r not in known_temp_ids and r not in catalog]
            if bad:
                errors.append(f"{where}: unknown item refs {bad}")
            elif refs and not (set(refs) & known_temp_ids):
                errors.append(f"{where}: item_refs have no photo crop (the executor can only compose "
                              f"detected temp_ids, not catalog ids) — drop item_refs or use a temp_id")
            has_visual = True

        elif t == "send_campaign":
            if a.get("segment_id") not in known_segments:
                errors.append(f"{where}: segment_id must come from a find_customers call "
                              f"(known: {sorted(known_segments)})")
            if a.get("attach_visual") and not has_visual:
                errors.append(f"{where}: attach_visual=true but no generate_visual action before it")
            has_template = bool(a.get("template"))
            if has_template and a.get("attach_visual"):
                # Approved WhatsApp templates carry text only: the card must not promise a visual nobody gets.
                errors.append(f"{where}: attach_visual must be false with a template "
                              f"(template messages are text-only); keep generate_visual for the owner")
            if has_template and a.get("message"):
                errors.append(f"{where}: message must be omitted when template is set "
                              f"(the template body is used)")
            if has_template:
                tmpl = known_templates.get(a["template"])
                if tmpl is None:
                    errors.append(f"{where}: template must come from a list_campaign_templates call "
                                  f"(known: {sorted(known_templates)})")
                else:
                    _check_template_variables(a, tmpl, where, errors)
                    raw_vars = a.get("variables") if isinstance(a.get("variables"), dict) else {}
                    rendered = render_campaign_template(
                        tmpl.get("body", ""),
                        {"1": "Müşteri", **{str(k): v for k, v in raw_vars.items()
                                           if isinstance(v, str)}})
                    _check_promised_numbers(where, rendered, plan_pcts, plan_prices, errors)
                    for v in raw_vars.values():
                        if isinstance(v, str) and _OFFER_WORDS_RE.search(v):
                            offer_texts.append((where, v))
                            break
            elif needs_template:
                errors.append(f"{where}: template is required with this backend "
                              f"(call list_campaign_templates and fill its variables)")
            else:
                msg = a.get("message") or ""
                if not msg:
                    errors.append(f"{where}: message is required "
                                  f"(or pick a template from list_campaign_templates)")
                elif len(msg) > 350:
                    errors.append(f"{where}: message too long ({len(msg)} > 350 chars)")
                else:
                    _check_no_contacts(where, "message", msg, errors)
                _check_promised_numbers(where, msg, plan_pcts, plan_prices, errors)
                if _OFFER_WORDS_RE.search(msg):
                    offer_texts.append((where, msg))

        elif t == "publish_social_post":
            caption = a.get("caption") or ""
            if not caption:
                errors.append(f"{where}: caption is required (what the owner will read on the card)")
            elif len(caption) > MAX_CAPTION_CHARS:
                errors.append(f"{where}: caption too long ({len(caption)} > {MAX_CAPTION_CHARS} chars)")
            else:
                tags = len(re.findall(r"#\w", caption))
                if tags > MAX_HASHTAGS:
                    errors.append(f"{where}: too many hashtags ({tags} > {MAX_HASHTAGS})")
                if _URL_RE.search(caption):
                    errors.append(f"{where}: caption must not contain a URL")
                if _contains_phone(caption):
                    errors.append(f"{where}: caption must not contain a phone number")
                _check_promised_numbers(where, caption, plan_pcts, plan_prices, errors)
            plats = a.get("platforms")
            if not isinstance(plats, list) or not plats:
                errors.append(f"{where}: platforms is required "
                              f"(a non-empty subset of {', '.join(SOCIAL_PLATFORMS)})")
            else:
                bad = [p for p in plats if p not in SOCIAL_PLATFORMS]
                if bad:
                    errors.append(f"{where}: unknown platforms {bad} "
                                  f"(allowed: {', '.join(SOCIAL_PLATFORMS)})")
            if a.get("publish_at"):
                try:
                    when = _iso_dt(a["publish_at"])
                except (ValueError, TypeError):
                    errors.append(f"{where}: publish_at must be an ISO datetime like 2026-11-01T10:00")
                else:
                    now = datetime.now()
                    if when < now + timedelta(minutes=5):
                        errors.append(f"{where}: publish_at must be at least 5 minutes in the future")
                    elif when > now + timedelta(days=30):
                        errors.append(f"{where}: publish_at must be within 30 days")
            if not a.get("visual_prompt"):
                errors.append(f"{where}: visual_prompt is required (the preview is generated pre-approval)")
            refs = a.get("item_refs") or []
            bad = [r for r in refs if r not in new_refs and r not in known_temp_ids and r not in catalog]
            if bad:
                errors.append(f"{where}: unknown item refs {bad} (add_catalog_item must come first)")

        elif t in APPOINTMENT_TYPES:
            if not a.get("label"):
                errors.append(f"{where}: label is required (what the owner will read on the card)")
            if t == "create_appointment":
                if a.get("customer_id") not in known_customers:
                    errors.append(f"{where}: customer_id must come from a find_customer call "
                                  f"(known: {sorted(known_customers)})")
                svc = catalog.get(a.get("item_ref") or "")
                if not svc:
                    errors.append(f"{where}: item_ref must be an existing catalog id (see list_catalog)")
                elif svc.get("type") != "service":
                    errors.append(f"{where}: {a.get('item_ref')} is not a service")
                if not a.get("staff"):
                    errors.append(f"{where}: staff is required (see get_store_profile)")
            else:
                if a.get("appointment_id") not in known_appointments:
                    errors.append(f"{where}: appointment_id must come from a find_appointments "
                                  f"or get_schedule call (known: {sorted(known_appointments)})")
            if t not in ("cancel_appointment", "complete_appointment"):
                try:
                    starts = _iso_dt(a.get("starts") or "")
                except (ValueError, TypeError):
                    errors.append(f"{where}: starts must be an ISO datetime like 2026-10-31T15:00")
                else:
                    if starts <= datetime.now():
                        errors.append(f"{where}: starts must be in the future")
            if t == "complete_appointment":
                amount = a.get("amount")
                if amount is None:
                    svc = (appointment_services or {}).get(a.get("appointment_id") or "")
                    prices = sorted({float(item["price"]) for item in catalog.values()
                                     if item.get("name") == svc and _is_number(item.get("price"))})
                    if len(prices) == 1:
                        # Default: charge the service's catalog price (the owner said nothing else).
                        a["amount"] = prices[0]
                    else:
                        errors.append(f"{where}: amount is required (the service price is not "
                                      "unique in this run's catalog)")
                elif not _is_number(amount) or not 0 < amount <= MAX_APPT_AMOUNT:
                    errors.append(f"{where}: amount must be more than 0 and at most "
                                  f"{MAX_APPT_AMOUNT}")
                if a.get("method") is not None and a.get("method") not in APPT_METHODS:
                    errors.append(f"{where}: method must be one of {' | '.join(APPT_METHODS)}")
                _check_crm_note(where, a.get("note"), errors)

        elif t == "apply_bulk_price":
            if not a.get("label"):
                errors.append(f"{where}: label is required (what the owner will read on the card)")
            pv = known_previews.get(a.get("preview_id") or "")
            if pv is None:
                errors.append(f"{where}: preview_id must come from a preview_bulk_price call "
                              f"(known: {sorted(known_previews)})")
            else:
                rows = pv.get("items") or []
                if not 1 <= len(rows) <= 200:
                    errors.append(f"{where}: preview has {len(rows)} items, need 1..200")
                for row in rows:
                    old, new = row.get("old_price"), row.get("new_price")
                    if not isinstance(new, (int, float)) or new <= 0:
                        errors.append(f"{where}: {row.get('id')}: new price must be positive")
                    elif not isinstance(old, (int, float)) or old <= 0:
                        errors.append(f"{where}: {row.get('id')}: old price is invalid")
                    elif not -0.5 <= (new - old) / old <= 0.5:
                        errors.append(f"{where}: {row.get('id')}: change {old:g}→{new:g} exceeds ±50%")

        elif t == "adjust_loyalty":
            if not a.get("label"):
                errors.append(f"{where}: label is required (what the owner will read on the card)")
            if a.get("customer_id") not in known_customers:
                errors.append(f"{where}: customer_id must come from a find_customer call "
                              f"(known: {sorted(known_customers)})")
            pts = a.get("points")
            if isinstance(pts, bool) or not isinstance(pts, int):
                errors.append(f"{where}: points must be an integer")
            elif not 1 <= abs(pts) <= 1000:
                errors.append(f"{where}: points must be 1..1000 by magnitude (negative subtracts)")
            if not a.get("reason"):
                errors.append(f"{where}: reason is required (why the points change)")

        elif t == "ads_action":
            if not a.get("label"):
                errors.append(f"{where}: label is required (what the owner will read on the card)")
            if a.get("campaign_id") not in known_campaigns:
                errors.append(f"{where}: campaign_id must come from a get_ads_performance call "
                              f"(known: {sorted(known_campaigns)})")
            act = a.get("action")
            if act not in ADS_ACTIONS:
                errors.append(f"{where}: action must be one of {sorted(ADS_ACTIONS)}")
            elif act in ("increase_budget", "decrease_budget"):
                delta = a.get("delta_pct")
                if isinstance(delta, bool) or not isinstance(delta, int):
                    errors.append(f"{where}: delta_pct must be an integer for budget actions")
                elif not 1 <= delta <= 20:
                    errors.append(f"{where}: delta_pct must be 1..20")

        elif t in CRM_TYPES:
            if not a.get("label"):
                errors.append(f"{where}: label is required (what the owner will read on the card)")
            if t in ("create_customer", "create_lead", "create_task"):
                ref = a.get("ref")
                if isinstance(ref, str) and ref:
                    if ref in crm_refs or ref in known_customers | known_leads | known_tasks:
                        errors.append(f"{where}: ref {ref!r} is already used")
                    else:
                        crm_refs[ref] = {"create_customer": "customer", "create_lead": "lead",
                                         "create_task": "task"}[t]
            else:
                key, kind, tool, known = {
                    "update_customer": ("customer_id", "customer", "find_customer", known_customers),
                    "set_lead_stage": ("lead_id", "lead", "find_leads", known_leads),
                    "add_lead_note": ("lead_id", "lead", "find_leads", known_leads),
                    "complete_task": ("task_id", "task", "list_tasks", known_tasks),
                }[t]
                value = a.get(key)
                seen = isinstance(value, str) and (value in known or crm_refs.get(value) == kind)
                if not seen:
                    errors.append(f"{where}: {key} must come from a {tool} call in this run or a "
                                  f"ref created earlier (known: {sorted(known)})")
            if t in ("create_customer", "update_customer"):
                _check_crm_name(where, a.get("name"), errors, required=t == "create_customer")
                _check_crm_note(where, a.get("note"), errors)
                if t == "create_customer":
                    _check_crm_tags(where, "tags", a.get("tags"), errors)
                else:
                    _check_crm_tags(where, "add_tags", a.get("add_tags"), errors)
                    _check_crm_tags(where, "remove_tags", a.get("remove_tags"), errors)
            elif t == "create_lead":
                _check_crm_name(where, a.get("name"), errors)
                _check_crm_note(where, a.get("note"), errors)
                _check_crm_tags(where, "tags", a.get("tags"), errors)
                v = a.get("estimated_value")
                if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))
                                      or v < 0):
                    errors.append(f"{where}: estimated_value must be 0 or more")
            elif t == "set_lead_stage":
                if a.get("stage") not in LEAD_STAGES:
                    errors.append(f"{where}: stage must be one of {' | '.join(LEAD_STAGES)}")
            elif t == "add_lead_note":
                if not a.get("note"):
                    errors.append(f"{where}: note is required")
                else:
                    _check_crm_note(where, a.get("note"), errors)
            elif t == "create_task":
                title = a.get("title")
                if not title:
                    errors.append(f"{where}: title is required")
                elif not isinstance(title, str) or len(title) > MAX_TITLE_CHARS:
                    errors.append(f"{where}: title must be {MAX_TITLE_CHARS} chars or less")
                due = str(a.get("due_date") or "")
                try:
                    day = date.fromisoformat(due[:10])
                    if len(due) > 10:
                        datetime.fromisoformat(due)  # validates the time part too
                except ValueError:
                    errors.append(f"{where}: due_date must be an ISO date or datetime (YYYY-MM-DD[THH:MM])")
                else:
                    if day < date.today():
                        errors.append(f"{where}: due_date is in the past")
                    elif day > date.today() + timedelta(days=365):
                        errors.append(f"{where}: due_date is more than a year out")
                for key, kind, tool, known in (
                        ("customer_id", "customer", "find_customer", known_customers),
                        ("lead_id", "lead", "find_leads", known_leads)):
                    value = a.get(key)
                    seen = (value is None or isinstance(value, str)
                            and (value in known or crm_refs.get(value) == kind))
                    if not seen:
                        errors.append(f"{where}: {key} must come from a {tool} call in this run or "
                                      f"a ref created earlier (known: {sorted(known)})")

        elif t == "create_quote_draft":
            if not a.get("label"):
                errors.append(f"{where}: label is required (what the owner will read on the card)")
            cust, lead = a.get("customer_id"), a.get("lead_id")
            if bool(cust) == bool(lead):
                errors.append(f"{where}: set exactly one of customer_id, lead_id")
            elif cust and cust not in known_customers:
                errors.append(f"{where}: customer_id must come from a find_customer call "
                              f"(known: {sorted(known_customers)})")
            elif lead and lead not in known_leads:
                errors.append(f"{where}: lead_id must come from a find_leads call "
                              f"(known: {sorted(known_leads)})")
            rows = a.get("items")
            if not isinstance(rows, list) or not rows:
                errors.append(f"{where}: items must be a non-empty list of quote rows")
            else:
                for j, row in enumerate(rows):
                    if not isinstance(row, dict):
                        errors.append(f"{where}: items[{j}] must be an object")
                        continue
                    item = catalog.get(row.get("catalog_item_id") or "")
                    if not item:
                        errors.append(f"{where}: items[{j}]: unknown catalog_item_id "
                                      "(see list_catalog)")
                        continue
                    qty = _coerce_number(row.get("qty"), True)
                    if isinstance(qty, int) and not isinstance(qty, bool):
                        row["qty"] = qty
                    if not isinstance(row.get("qty"), int) or isinstance(row.get("qty"), bool) \
                            or not 1 <= row["qty"] <= 100:
                        errors.append(f"{where}: items[{j}]: qty must be 1..100")
                    unit = _coerce_number(row.get("unit_price"), False)
                    if _is_number(unit):
                        row["unit_price"] = unit
                    unit = row.get("unit_price")
                    if unit is not None:
                        if not _is_number(unit) or unit < 0:
                            errors.append(f"{where}: items[{j}]: unit_price must be 0 or more")
                        elif _is_number(item.get("price")) \
                                and unit < item["price"] * MIN_QUOTE_PRICE_RATIO:
                            errors.append(f"{where}: items[{j}]: unit_price is below half the "
                                          f"catalog price ({item['price']:g} ₺)")
            days = a.get("valid_days")
            if days is not None and (not isinstance(days, int) or isinstance(days, bool)
                                      or not 1 <= days <= MAX_VALID_DAYS):
                errors.append(f"{where}: valid_days must be 1..{MAX_VALID_DAYS}")
            _check_crm_note(where, a.get("note"), errors)

        elif t == "teach_chatbot":
            if not a.get("label"):
                errors.append(f"{where}: label is required (what the owner will read on the card)")
            question, answer = a.get("question"), a.get("answer")
            for field, value, maxi in (("question", question, MAX_QUESTION_CHARS),
                                       ("answer", answer, MAX_ANSWER_CHARS)):
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{where}: {field} is required")
                else:
                    if len(value) > maxi:
                        errors.append(f"{where}: {field} too long ({len(value)} > {maxi} chars)")
                    _check_no_contacts(where, field, value, errors)
            _check_answer_prices(where, answer, catalog, plan_prices, errors)

    # Plan/summary consistency: an audience was built and the summary promises a message, but nothing is sent.
    types = {a.get("type") for a in actions}
    if "create_customer" in types and "send_campaign" in types:
        # Segments are opaque at plan time: the new customer (consent false) might be messaged.
        errors.append("create_customer cannot share a plan with send_campaign (new customers have no "
                      "marketing consent, so they cannot be messaged)")
    if "create_discount" not in types:
        for where, text in offer_texts:
            word = _OFFER_WORDS_RE.search(text).group(0)  # type: ignore[union-attr]
            errors.append(f"{where}: {word!r} promises a deal but the plan has no create_discount action")
    summary = proposal.get("summary_tr") or ""
    if (known_segments and "send_campaign" not in types and _CAMPAIGN_WORDS_RE.search(summary)
            and not _NEGATED_CAMPAIGN_RE.search(summary)):
        errors.append("summary_tr talks about a campaign/message and a customer segment was built, but there is "
                      "no send_campaign action — add it (with the segment_id) or rewrite summary_tr to say "
                      "clearly that no message will be sent")
    return errors
