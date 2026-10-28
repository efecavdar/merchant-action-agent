"""Human-in-the-loop approval card."""

from __future__ import annotations

from typing import Any, Callable

from .pipeline import MediaContext

_ICON = {"add_catalog_item": "🛍️", "create_discount": "🏷️", "generate_visual": "🖼️", "send_campaign": "📣",
         "create_appointment": "📅", "reschedule_appointment": "🔁", "cancel_appointment": "❌"}
_APPT_VERB = {"create_appointment": "Randevu", "reschedule_appointment": "Randevu taşınıyor",
              "cancel_appointment": "Randevu iptal"}


def render_card(proposal: dict[str, Any], ctx: MediaContext, segment_counts: dict[str, int]) -> str:
    """The text the merchant sees (WhatsApp-style)."""
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
        elif t in _APPT_VERB:
            lines.append(f"{ic} {_APPT_VERB[t]}: {a.get('label') or a.get('appointment_id') or ''}")
        elif t == "apply_bulk_price":
            lines.append(f"💸 Toplu fiyat: {a.get('label') or a.get('preview_id')}")
        elif t == "adjust_loyalty":
            lines.append(f"⭐ Puan: {a.get('label') or a.get('customer_id')}")
        elif t == "ads_action":
            lines.append(f"📢 Reklam: {a.get('label') or a.get('campaign_id')}")
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
