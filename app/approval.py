"""Human-in-the-loop approval card."""

from __future__ import annotations

import re
from typing import Any, Callable

from . import i18n
from .pipeline import MediaContext
from .tools.backend import render_campaign_template

_ICON = {"add_catalog_item": "🛍️", "create_discount": "🏷️", "generate_visual": "🖼️", "send_campaign": "📣",
         "create_appointment": "📅", "reschedule_appointment": "🔁", "cancel_appointment": "❌",
         "publish_social_post": "📱"}

_NAME_CHARS = 60   # = the validator's name/variable limit: the owner sees everything a customer gets
_DESC_CHARS = 120  # catalog descriptions show what will be written, so they get more room
_PROMPT_CHARS = 80

# Controls, Unicode line/paragraph separators, NEL and bidi/format characters (U+202E can reorder a line).
# ZWJ stays so joined emoji survive.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u00ad\u061c\u200b\u200c\u200e\u200f\u2028-\u202e\u2060-\u2069\ufeff]+")


def clean_display(text: Any, max_chars: int = _NAME_CHARS) -> str:
    """Make a third-party string safe for one card line: control chars and newlines collapse
    to one space, WhatsApp markup chars are stripped (a name must not reformat the card),
    and the result is truncated."""
    s = _CONTROL_RE.sub(" ", str(text or "")).strip()
    s = s.replace("*", "").replace("_", "").replace("~", "")
    return s if len(s) <= max_chars else s[:max_chars - 1].rstrip() + "…"


def card_facts(tool_log: list[dict[str, Any]]) -> dict[str, Any]:
    """What the tools actually returned in this run. Card lines for ids are rendered from these facts and the
    action's own fields — never from model-written labels, so the owner approves what will really run."""
    f: dict[str, Any] = {"previews": {}, "customers": {}, "campaigns": {}, "appointments": {},
                       "items": {}, "templates": {}, "campaign_previews": {}, "members": {}}
    for e in tool_log:
        r = e.get("result")
        if not isinstance(r, dict):
            continue
        tool = e.get("tool")
        if tool == "preview_bulk_price" and r.get("preview_id"):
            f["previews"][r["preview_id"]] = r
        elif tool == "find_customer":
            f["customers"].update({m["id"]: m.get("name", "?") for m in r.get("matches") or []})
        elif tool == "get_ads_performance":
            f["campaigns"].update({c["id"]: c for c in r.get("campaigns") or []})
        elif tool == "find_appointments":
            f["appointments"].update({a["id"]: a for a in r.get("appointments") or []})
        elif tool == "list_catalog":
            f["items"].update({i["id"]: i for i in r.get("items") or []})
        elif tool == "list_campaign_templates":
            f["templates"].update({t["name"]: t for t in r.get("templates") or []})
        elif tool == "preview_campaign":
            if r.get("segment_id") and r.get("template"):
                f["campaign_previews"][(r["segment_id"], r["template"])] = r
        elif tool == "find_loyalty_member":
            f["members"].update({m["member_id"]: m for m in r.get("members") or []
                                 if isinstance(m, dict) and m.get("member_id")})
    return f


def _when(iso: str | None) -> str:
    """2026-10-31T15:00 → 31.10 15:00 (unparseable values are shown as-is)."""
    s = iso or "?"
    return f"{s[8:10]}.{s[5:7]} {s[11:16]}" if len(s) >= 16 and s[10] == "T" else s


def _fact_line(t: str, a: dict[str, Any], f: dict[str, Any], lang: str = "tr") -> str:
    if t == "apply_bulk_price":
        p = f["previews"].get(a.get("preview_id"))
        if not p:
            return i18n.t("fact.bulk_nopreview", lang)
        items = p.get("items") or []
        head = ", ".join(f"{clean_display(i['name'])} {i['old_price']:,.0f}→{i['new_price']:,.0f} ₺"
                         for i in items[:3])
        more = i18n.t("fact.bulk_more", lang).format(k=len(items) - 3) if len(items) > 3 else ""
        unit = i18n.t("card.item_one" if len(items) == 1 else "card.item_many", lang)
        return i18n.t("fact.bulk", lang).format(n=len(items), unit=unit, head=head, more=more)
    if t == "adjust_loyalty":
        pts = a.get("points")
        sign = f"+{pts}" if isinstance(pts, int) and pts > 0 else f"{pts}"
        reason = f" ({clean_display(a['reason'])})" if a.get("reason") else ""
        member = f["members"].get(a.get("customer_id")) or {}
        old = member.get("points")
        if member.get("name") and isinstance(old, int) and isinstance(pts, int):
            who, change = clean_display(member["name"]), f"{old}→{old + pts}"
        else:
            who = clean_display(f["customers"].get(a.get("customer_id"), a.get("customer_id")))
            change = sign
        return i18n.t("fact.loyalty", lang).format(who=who, change=change, reason=reason)
    if t == "ads_action":
        c = f["campaigns"].get(a.get("campaign_id")) or {}
        name = clean_display(c.get("name") or a.get("campaign_id"))
        act, d = a.get("action"), a.get("delta_pct")
        if act in ("increase_budget", "decrease_budget") and isinstance(d, int):
            old = c.get("daily_budget")
            new = None if old is None else round(old * (1 + (d if act == "increase_budget" else -d) / 100))
            verb = (i18n.t("fact.ad_budget_up" if act == "increase_budget" else "fact.ad_budget_down",
                            lang).format(d=d)
                    + (f" ({old:,.0f}→{new:,.0f} ₺)" if old is not None else ""))
        else:
            verb = {"pause": i18n.t("fact.ad_pause", lang),
                    "resume": i18n.t("fact.ad_resume", lang)}.get(act, str(act))
        return i18n.t("fact.ad", lang).format(name=name, verb=verb)
    if t == "create_appointment":
        svc = clean_display((f["items"].get(a.get("item_ref")) or {}).get("name", a.get("item_ref")))
        who = clean_display(f["customers"].get(a.get("customer_id"), a.get("customer_id")))
        return i18n.t("fact.appt", lang).format(who=who, svc=svc, staff=clean_display(a.get("staff")),
                                               when=_when(a.get("starts")))
    ap = f["appointments"].get(a.get("appointment_id")) or {}
    base = (f"{clean_display(ap.get('customer', '?'))} — {clean_display(ap.get('service', '?'))}, "
            f"{_when(ap.get('starts'))}")
    if t == "reschedule_appointment":
        staff = f", {clean_display(a['staff'])}" if a.get("staff") else ""
        return i18n.t("fact.appt_move", lang).format(base=base, when=_when(a.get("starts")), staff=staff)
    return i18n.t("fact.appt_cancel", lang).format(base=base)


_FACT_TYPES = {"apply_bulk_price", "adjust_loyalty", "ads_action", "create_appointment",
               "reschedule_appointment", "cancel_appointment"}


def render_card(proposal: dict[str, Any], ctx: MediaContext, segment_counts: dict[str, int],
                facts: dict[str, Any] | None = None, lang: str = "tr") -> str:
    """The text the merchant sees (WhatsApp-style). Action lines first, then the model's note,
    so the prose cannot frame what is actually about to run. Labels follow `lang`; facts
    (numbers, names, dates) and customer-facing quotes stay as they are."""
    facts = facts or card_facts([])
    products = {p.temp_id: p for p in ctx.products}
    new_items: dict[str, str] = {}  # temp_id / ref -> display name, for discounts below
    for a in proposal["actions"]:
        if a.get("type") == "add_catalog_item":
            handle = a.get("temp_id") or a.get("ref")
            if handle:
                card = products.get(a.get("temp_id") or "")
                new_items[handle] = a.get("name") or (card.name if card else "?")
    marker = i18n.t("card.customer_marker", lang)  # customer quotes stay Turkish, labelled as such
    lines = [i18n.t("card.title", lang), ""]
    for a in proposal["actions"]:
        t = a["type"]; ic = _ICON.get(t, "•")
        if t == "add_catalog_item":
            kind = i18n.t("card.service" if a.get("item_type") == "service" else "card.product", lang)
            card = products.get(a.get("temp_id") or "")
            name = clean_display(a.get("name") or (card.name if card else "?"))
            # Same fallbacks the executor writes: explicit fields win, vision fills the gaps.
            category = clean_display(a.get("category") or (card.category if card else ""))
            desc = clean_display(a.get("description") or (card.description if card else ""),
                                 _DESC_CHARS)
            extra = f", {a['duration_minutes']} dk" if a.get("duration_minutes") else ""
            src = " 📷" if a.get("temp_id") else ""
            line = f"{ic} {kind}: {name} — {a.get('price'):,.0f} ₺{extra}{src}"
            if category:
                line += f" · {category}"
            if desc:
                line += f" — {desc}"
            lines.append(line)
        elif t == "create_discount":
            refs = a.get("item_refs") or []
            resolved = [clean_display(new_items.get(r) or (facts["items"].get(r) or {}).get("name") or r)
                        for r in refs]
            shown = ", ".join(resolved[:5])
            if len(resolved) > 5:
                unit = i18n.t("card.item_one" if len(resolved) - 5 == 1 else "card.item_many", lang)
                shown += f" +{len(resolved) - 5} {unit}"
            unit = i18n.t("card.item_one" if len(refs) == 1 else "card.item_many", lang)
            lines.append(f"{ic} {i18n.t('card.discount_label', lang)}: %{a.get('percent')} "
                         f"({a.get('starts')} → {a.get('ends')}) — {shown or f'{len(refs)} {unit}'}")
        elif t == "generate_visual":
            prompt = clean_display(a.get("visual_prompt"), _PROMPT_CHARS)
            lines.append(f"{ic} {i18n.t('card.visual', lang)}" + (f": “{prompt}”" if prompt else ""))
        elif t == "send_campaign":
            n = segment_counts.get(a.get("segment_id"), "?")
            attach = i18n.t("card.attach", lang) if a.get("attach_visual") else ""
            pv = facts["campaign_previews"].get((a.get("segment_id"), a.get("template")))
            if pv and a.get("template"):
                # Dry-run facts win over the planned counts: real eligible + skipped numbers.
                rec = pv.get("recipients")
                head = (i18n.t("card.going", lang).format(rec=rec) if isinstance(rec, int)
                        else i18n.t("card.going_unknown", lang))
                sk = pv.get("skipped") or {}
                nc, ot = sk.get("no_consent") or 0, sk.get("other") or 0
                if nc:
                    head += i18n.t("card.skip_consent", lang).format(nc=nc)
                if ot:
                    head += i18n.t("card.skip_other", lang).format(ot=ot)
                lines.append(f"{ic} WhatsApp: {head}{attach}")
                lines.append(f"   “{pv.get('template_text', '')}” {marker}")
                continue
            lines.append(i18n.t("card.wa_message", lang).format(icon=ic, n=n, attach=attach))
            if a.get("template"):
                tmpl = facts["templates"].get(a["template"])
                if tmpl:
                    display_vars = {k: clean_display(v)
                                    for k, v in ((a.get("variables") or {}).items())}
                    rendered = render_campaign_template(
                        tmpl["body"], {"1": "‹müşteri adı›", **display_vars})
                    lines.append(f"   “{rendered}” {marker}")
                else:
                    missing = i18n.t("card.template_missing", lang).format(name=a["template"])
                    lines.append(f"   “{missing}”")
            else:
                lines.append(f"   “{a.get('message')}” {marker}")
        elif t == "publish_social_post":
            plats = ", ".join(p.capitalize() for p in (a.get("platforms") or []))
            when = (i18n.t("card.scheduled", lang).format(when=_when(a["publish_at"]))
                    if a.get("publish_at") else "")
            lines.append(i18n.t("card.post", lang).format(icon=ic, plats=plats, when=when))
            lines.append(f"   “{a.get('caption')}” {marker}")  # verbatim: exactly what will be published
        elif t in _FACT_TYPES:
            lines.append(_fact_line(t, a, facts, lang))
    for note in proposal.get("_social_notes") or []:
        lines.append(f"⚠️ {note}")
    lines += ["", i18n.t("card.note", lang).format(note=proposal["summary_tr"].strip()), "",
              i18n.t("card.approve", lang)]
    return "\n".join(lines)


def ask_console(card: str) -> bool:
    print("\n" + "─" * 60 + "\n" + card + "\n" + "─" * 60)
    ans = input("> ").strip().lower()
    return ans in {"evet", "e", "yes", "y", "onay", "ok", "tamam"}


def auto_approve(card: str) -> bool:
    print("\n" + "─" * 60 + "\n" + card + "\n" + "─" * 60 + "\n> evet (auto-approve)")
    return True


Approver = Callable[[str], bool]
