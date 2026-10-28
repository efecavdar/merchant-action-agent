"""Human-in-the-loop approval card."""

from __future__ import annotations

from typing import Any, Callable

from .pipeline import MediaContext

_ICON = {"add_catalog_item": "🛍️", "create_discount": "🏷️", "generate_visual": "🖼️", "send_campaign": "📣",
         "create_appointment": "📅", "reschedule_appointment": "🔁", "cancel_appointment": "❌"}


def card_facts(tool_log: list[dict[str, Any]]) -> dict[str, Any]:
    """What the tools actually returned in this run. Card lines for ids are rendered from these facts and the
    action's own fields — never from model-written labels, so the owner approves what will really run."""
    f: dict[str, Any] = {"previews": {}, "customers": {}, "campaigns": {}, "appointments": {}, "items": {}}
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
    return f


def _when(iso: str | None) -> str:
    """2026-10-31T15:00 → 31.10 15:00 (unparseable values are shown as-is)."""
    s = iso or "?"
    return f"{s[8:10]}.{s[5:7]} {s[11:16]}" if len(s) >= 16 and s[10] == "T" else s


def _fact_line(t: str, a: dict[str, Any], f: dict[str, Any]) -> str:
    if t == "apply_bulk_price":
        p = f["previews"].get(a.get("preview_id"))
        if not p:
            return "💸 Toplu fiyat: ⚠️ önizleme bulunamadı"
        items = p.get("items") or []
        head = ", ".join(f"{i['name']} {i['old_price']:,.0f}→{i['new_price']:,.0f} ₺" for i in items[:3])
        more = f" (+{len(items) - 3} kalem daha)" if len(items) > 3 else ""
        return f"💸 Toplu fiyat: {len(items)} kalem — {head}{more}"
    if t == "adjust_loyalty":
        pts = a.get("points")
        sign = f"+{pts}" if isinstance(pts, int) and pts > 0 else f"{pts}"
        reason = f" ({a['reason']})" if a.get("reason") else ""
        return f"⭐ Puan: {f['customers'].get(a.get('customer_id'), a.get('customer_id'))} {sign}{reason}"
    if t == "ads_action":
        c = f["campaigns"].get(a.get("campaign_id")) or {}
        name = c.get("name") or a.get("campaign_id")
        act, d = a.get("action"), a.get("delta_pct")
        if act in ("increase_budget", "decrease_budget") and isinstance(d, int):
            old = c.get("daily_budget")
            new = None if old is None else round(old * (1 + (d if act == "increase_budget" else -d) / 100))
            verb = (f"günlük bütçe %{d} {'artacak' if act == 'increase_budget' else 'azalacak'}"
                    + (f" ({old:,.0f}→{new:,.0f} ₺)" if old is not None else ""))
        else:
            verb = {"pause": "durdurulacak", "resume": "yeniden başlatılacak"}.get(act, str(act))
        return f"📢 Reklam: {name} — {verb}"
    if t == "create_appointment":
        svc = (f["items"].get(a.get("item_ref")) or {}).get("name", a.get("item_ref"))
        who = f["customers"].get(a.get("customer_id"), a.get("customer_id"))
        return f"📅 Randevu: {who} — {svc}, {a.get('staff')}, {_when(a.get('starts'))}"
    ap = f["appointments"].get(a.get("appointment_id")) or {}
    base = f"{ap.get('customer', '?')} — {ap.get('service', '?')}, {_when(ap.get('starts'))}"
    if t == "reschedule_appointment":
        staff = f", {a['staff']}" if a.get("staff") else ""
        return f"🔁 Randevu taşınıyor: {base} → {_when(a.get('starts'))}{staff}"
    return f"❌ Randevu iptal: {base}"


_FACT_TYPES = {"apply_bulk_price", "adjust_loyalty", "ads_action", "create_appointment",
               "reschedule_appointment", "cancel_appointment"}


def render_card(proposal: dict[str, Any], ctx: MediaContext, segment_counts: dict[str, int],
                facts: dict[str, Any] | None = None) -> str:
    """The text the merchant sees (WhatsApp-style)."""
    facts = facts or card_facts([])
    names = {p.temp_id: p.name for p in ctx.products}
    lines = ["*Çırak — onayına sunuyorum*", "", proposal["summary_tr"].strip(), ""]
    for a in proposal["actions"]:
        t = a["type"]; ic = _ICON.get(t, "•")
        if t == "add_catalog_item":
            kind = "Hizmet" if a.get("item_type") == "service" else "Ürün"
            name = a.get("name") or names.get(a.get("temp_id"), "?")
            extra = f", {a['duration_minutes']} dk" if a.get("duration_minutes") else ""
            src = " 📷" if a.get("temp_id") else ""
            lines.append(f"{ic} {kind}: {name} — {a.get('price'):,.0f} ₺{extra}{src}")
        elif t == "create_discount":
            lines.append(f"{ic} İndirim: %{a.get('percent')} ({a.get('starts')} → {a.get('ends')}) — "
                         f"{len(a.get('item_refs') or [])} kalem")
        elif t == "generate_visual":
            lines.append(f"{ic} Kampanya görseli üretilecek")
        elif t == "send_campaign":
            n = segment_counts.get(a.get("segment_id"), "?")
            lines.append(f"{ic} WhatsApp: {n} müşteriye mesaj")
            lines.append(f"   “{a.get('message')}”")
        elif t in _FACT_TYPES:
            lines.append(_fact_line(t, a, facts))
    lines += ["", "Onaylıyor musun? (evet / hayır)"]
    return "\n".join(lines)


def ask_console(card: str) -> bool:
    print("\n" + "─" * 60 + "\n" + card + "\n" + "─" * 60)
    ans = input("> ").strip().lower()
    return ans in {"evet", "e", "yes", "y", "onay", "ok", "tamam"}


def auto_approve(card: str) -> bool:
    print("\n" + "─" * 60 + "\n" + card + "\n" + "─" * 60 + "\n> evet (auto-approve)")
    return True


Approver = Callable[[str], bool]
