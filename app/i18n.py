"""Owner language: fixed strings (Turkish/English) + deterministic language detection.

The owner may write Turkish or English; every fixed owner-facing string is looked up here by key
and rendered in `Session.lang`. Customer-facing text (campaign messages, captions, catalog
names/descriptions) is always Turkish and never goes through this module.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

TR = "tr"
EN = "en"

STRINGS: dict[str, dict[str, str]] = {
    # -- core.py: greeting, commands, status -----------------------------------
    "help": {
        "tr": ("Merhaba, ben Çırak 👋 Sesli not ya da yazıyla ne istediğini söyle, istersen önce "
               "ürün fotoğrafı at.\n"
               "Örn: \"Bunları ekle, tanesi 850 lira, hafta sonu %15 indirim, uzun süredir "
               "gelmeyenlere haber ver.\"\n"
               "Hiçbir şey senin *evet*'in olmadan yapılmaz."),
        "en": ("Hi, I'm Çırak 👋 Tell me what you need by voice note or text, or send product "
               "photos first.\n"
               "E.g.: \"Add these, 850 lira each, 15% off this weekend, tell customers who haven't "
               "visited in a while.\"\n"
               "Nothing happens without your *yes*."),
    },
    "business.switched": {
        "tr": "Tamam, artık *{name}* (demo) için çalışıyorum.",
        "en": "Done, now working for *{name}* (demo).",
    },
    "kedd.off": {
        "tr": "KEDD bağlantısı bu ortamda kapalı.",
        "en": "KEDD connection is off in this environment.",
    },
    "kedd.switched_named": {
        "tr": "Tamam, artık KEDD'deki işletmen (*{name}*) için çalışıyorum.",
        "en": "Done, now working for your KEDD business (*{name}*).",
    },
    "kedd.switched": {
        "tr": "Tamam, artık KEDD'deki işletmen için çalışıyorum.",
        "en": "Done, now working for your KEDD business.",
    },
    "reset.done": {
        "tr": "Sıfırladım. Demo verisi baştan yüklendi.",
        "en": "Reset done. Demo data reloaded.",
    },
    "approval.race": {
        "tr": ("Bu onay, kart sana ulaşmadan önce gelmiş görünüyor. Kartı okuyup tekrar *evet* "
               "yazar mısın?"),
        "en": ("This approval seems to have arrived before the card reached you. Could you read the card "
               "and write *yes* again?"),
    },
    "approval.cancelled": {
        "tr": "Tamam, hiçbir şey yapmadım. Planı iptal ettim.",
        "en": "OK, I did nothing. The plan is cancelled.",
    },
    "photo.received": {
        "tr": "📷 {n} {unit} aldım. Ne yapayım? Sesli not ya da mesajla söyle.",
        "en": "📷 Got {n} {unit}. What should I do? Tell me by voice note or message.",
    },
    "photo.unit_one": {"tr": "fotoğraf", "en": "photo"},
    "photo.unit_many": {"tr": "fotoğraf", "en": "photos"},
    "error.unreadable": {
        "tr": "Bunu okuyamadım. Yazı, sesli not ya da fotoğraf gönderebilirsin.",
        "en": "Couldn't read that. You can send text, a voice note or a photo.",
    },
    "plan.superseded": {
        "tr": "Önceki planı iptal ettim, yeni isteğine bakıyorum.",
        "en": "Dropped the previous plan, looking at your new request.",
    },
    "notify.looking": {"tr": "⏳ Bakıyorum…", "en": "⏳ Looking into it…"},
    "notify.photo_suffix": {"tr": " ({n} {unit})", "en": " ({n} {unit})"},
    "notify.voice": {"tr": "🎙️ Anladığım: “{text}”", "en": "🎙️ I understood: “{text}”"},
    "notify.approved": {"tr": "✅ Onaylandı, uyguluyorum…", "en": "✅ Approved, applying it…"},
    # -- core.py: model-free report fallback ------------------------------------
    "report.fallback_title": {"tr": "Yapılanlar:", "en": "Done:"},
    "report.recipients": {"tr": "{n} kişi", "en": "{n} people"},
    "report.failed": {
        "tr": "\n\n⚠️ Bir adım başarısız oldu, sonrasını güvenlik için durdurdum: {err}",
        "en": "\n\n⚠️ One step failed, so I stopped the rest for safety: {err}",
    },
    "step.add_catalog_item": {"tr": "Kataloğa eklendi", "en": "Added to catalog"},
    "step.create_discount": {"tr": "İndirim tanımlandı", "en": "Discount created"},
    "step.generate_visual": {"tr": "Kampanya görseli hazırlandı", "en": "Campaign visual prepared"},
    "step.send_campaign": {"tr": "Kampanya gönderildi", "en": "Campaign sent"},
    "step.publish_social_post": {"tr": "Sosyal medya gönderisi paylaşıldı",
                                 "en": "Social post published"},
    "step.create_appointment": {"tr": "Randevu oluşturuldu", "en": "Appointment created"},
    "step.reschedule_appointment": {"tr": "Randevu taşındı", "en": "Appointment moved"},
    "step.cancel_appointment": {"tr": "Randevu iptal edildi", "en": "Appointment cancelled"},
    "step.apply_bulk_price": {"tr": "Toplu fiyat güncellendi", "en": "Bulk prices updated"},
    "step.adjust_loyalty": {"tr": "Puan güncellendi", "en": "Points updated"},
    "step.ads_action": {"tr": "Reklam güncellendi", "en": "Ad updated"},
    # -- approval.py: card labels (facts stay as they are) -----------------------
    "card.title": {"tr": "*Çırak — onayına sunuyorum*", "en": "*Çırak — for your approval*"},
    "card.product": {"tr": "Ürün", "en": "Product"},
    "card.service": {"tr": "Hizmet", "en": "Service"},
    "card.discount_label": {"tr": "İndirim", "en": "Discount"},
    "card.item_one": {"tr": "kalem", "en": "item"},
    "card.item_many": {"tr": "kalem", "en": "items"},
    "card.visual": {"tr": "Kampanya görseli üretilecek", "en": "Campaign visual will be generated"},
    "card.wa_message": {
        "tr": "{icon} WhatsApp: {n} müşteriye mesaj{attach}",
        "en": "{icon} WhatsApp: message to {n} customers{attach}",
    },
    "card.attach": {"tr": " 📎 görsel eklenecek", "en": " 📎 visual will be attached"},
    "card.going": {"tr": "{rec} kişiye gidecek", "en": "going to {rec} people"},
    "card.going_unknown": {"tr": "müşteriye gidecek", "en": "going to customers"},
    "card.skip_consent": {"tr": ", {nc} kişi izinsiz olduğu için atlanacak",
                          "en": ", {nc} without consent will be skipped"},
    "card.skip_other": {"tr": ", {ot} kişi başka nedenle atlanacak",
                        "en": ", {ot} will be skipped for other reasons"},
    "card.template_missing": {"tr": "⚠️ şablon bulunamadı: {name}",
                                "en": "⚠️ template not found: {name}"},
    "card.post": {"tr": "{icon} {plats} gönderisi{when}", "en": "{icon} {plats} post{when}"},
    "card.scheduled": {"tr": " · {when}'de paylaşılacak", "en": " · will be posted at {when}"},
    "card.note": {"tr": "Çırak'ın notu: {note}", "en": "Çırak's note: {note}"},
    "card.approve": {"tr": "Onaylıyor musun? (evet / hayır)", "en": "Approve? (yes / no)"},
    "card.customer_marker": {"tr": "(müşteriye Türkçe gider)", "en": "(sent to customers in Turkish)"},
    # -- approval.py: fact lines --------------------------------------------------
    "fact.bulk": {"tr": "💸 Toplu fiyat: {n} {unit} — {head}{more}",
                  "en": "💸 Bulk prices: {n} {unit} — {head}{more}"},
    "fact.bulk_more": {"tr": " (+{k} kalem daha)", "en": " (+{k} more items)"},
    "fact.bulk_nopreview": {"tr": "💸 Toplu fiyat: ⚠️ önizleme bulunamadı",
                            "en": "💸 Bulk prices: ⚠️ preview not found"},
    "fact.loyalty": {"tr": "⭐ Puan: {who} {change}{reason}", "en": "⭐ Points: {who} {change}{reason}"},
    "fact.ad": {"tr": "📢 Reklam: {name} — {verb}", "en": "📢 Ad: {name} — {verb}"},
    "fact.ad_budget_up": {"tr": "günlük bütçe %{d} artacak", "en": "daily budget will increase by %{d}"},
    "fact.ad_budget_down": {"tr": "günlük bütçe %{d} azalacak",
                                "en": "daily budget will decrease by %{d}"},
    "fact.ad_pause": {"tr": "durdurulacak", "en": "will be paused"},
    "fact.ad_resume": {"tr": "yeniden başlatılacak", "en": "will be resumed"},
    "fact.appt": {"tr": "📅 Randevu: {who} — {svc}, {staff}, {when}",
                  "en": "📅 Appointment: {who} — {svc}, {staff}, {when}"},
    "fact.appt_move": {"tr": "🔁 Randevu taşınıyor: {base} → {when}{staff}",
                       "en": "🔁 Appointment moved: {base} → {when}{staff}"},
    "fact.appt_cancel": {"tr": "❌ Randevu iptal: {base}", "en": "❌ Appointment cancelled: {base}"},
    # -- brief.py -----------------------------------------------------------------
    "brief.greeting": {"tr": "Günaydın ☀️ ", "en": "Good morning ☀️ "},
    # -- executor progress lines (facts only: numbers, names, dates from the step record) --
    "progress.start": {"tr": "▶️ Başlıyorum — {n} {unit}", "en": "▶️ Starting — {n} {unit}"},
    "progress.step_one": {"tr": "adım", "en": "step"},
    "progress.step_many": {"tr": "adım", "en": "steps"},
    "progress.item_added": {"tr": "✅ {i}/{n} Ürün eklendi: {name} — {price} ₺",
                            "en": "✅ {i}/{n} Item added: {name} — {price} ₺"},
    "progress.service_added": {"tr": "✅ {i}/{n} Hizmet eklendi: {name} — {price} ₺",
                               "en": "✅ {i}/{n} Service added: {name} — {price} ₺"},
    "progress.discount": {"tr": "✅ {i}/{n} İndirim planlandı: %{p}, {dates}",
                          "en": "✅ {i}/{n} Discount created: {p}%, {dates}"},
    "progress.visual": {"tr": "✅ {i}/{n} Kampanya görseli hazır",
                            "en": "✅ {i}/{n} Campaign visual ready"},
    "progress.campaign": {"tr": "✅ {i}/{n} Kampanya: {k} kişiye gitti",
                          "en": "✅ {i}/{n} Campaign: sent to {k} people"},
    "progress.post_scheduled": {"tr": "✅ {i}/{n} {plats}: {day} {hm}{suffix} yayında",
                                "en": "✅ {i}/{n} {plats}: going live {day} at {hm}"},
    "progress.post_now": {"tr": "✅ {i}/{n} {plats}: paylaşıldı", "en": "✅ {i}/{n} {plats}: published"},
    "progress.appt_created": {"tr": "✅ {i}/{n} Randevu oluşturuldu: {when}",
                              "en": "✅ {i}/{n} Appointment created: {when}"},
    "progress.appt_moved": {"tr": "✅ {i}/{n} Randevu taşındı: {when}",
                            "en": "✅ {i}/{n} Appointment moved: {when}"},
    "progress.appt_cancelled": {"tr": "✅ {i}/{n} Randevu iptal edildi",
                                "en": "✅ {i}/{n} Appointment cancelled"},
    "progress.bulk": {"tr": "✅ {i}/{n} Toplu fiyat: {k} {unit} güncellendi",
                      "en": "✅ {i}/{n} Bulk prices: {k} {unit} updated"},
    "progress.loyalty": {"tr": "✅ {i}/{n} Puan: {delta} (toplam {total})",
                         "en": "✅ {i}/{n} Points: {delta} (total {total})"},
    "progress.ad": {"tr": "✅ {i}/{n} Reklam güncellendi", "en": "✅ {i}/{n} Ad updated"},
    "progress.failed": {"tr": "⛔ {i}/{n} {verb}{msg}{stop}", "en": "⛔ {i}/{n} {verb}{msg}{stop}"},
    "progress.failed_msg": {"tr": ": {msg}", "en": ": {msg}"},
    "progress.failed_stop": {"tr": " — kalan adımları durdurdum",
                                 "en": " — I stopped the remaining steps"},
    "progress.fail.add_catalog_item": {"tr": "Kataloga eklenemedi", "en": "Couldn't add to catalog"},
    "progress.fail.create_discount": {"tr": "İndirim yapılamadı", "en": "Couldn't create the discount"},
    "progress.fail.generate_visual": {"tr": "Görsel üretilemedi", "en": "Couldn't generate the visual"},
    "progress.fail.send_campaign": {"tr": "Kampanya gönderilemedi", "en": "Couldn't send the campaign"},
    "progress.fail.publish_social_post": {"tr": "Gönderi paylaşılamadı",
                                              "en": "Couldn't publish the post"},
    "progress.fail.create_appointment": {"tr": "Randevu oluşturulamadı",
                                         "en": "Couldn't create the appointment"},
    "progress.fail.reschedule_appointment": {"tr": "Randevu taşınamadı",
                                             "en": "Couldn't move the appointment"},
    "progress.fail.cancel_appointment": {"tr": "Randevu iptal edilemedi",
                                         "en": "Couldn't cancel the appointment"},
    "progress.fail.apply_bulk_price": {"tr": "Toplu fiyat güncellenemedi", "en": "Couldn't update prices"},
    "progress.fail.adjust_loyalty": {"tr": "Puan güncellenemedi", "en": "Couldn't update points"},
    "progress.fail.ads_action": {"tr": "Reklam güncellenemedi", "en": "Couldn't update the ad"},
    "progress.today": {"tr": "bugün", "en": "today"},
    "progress.tomorrow": {"tr": "yarın", "en": "tomorrow"},
    # -- core.py: KEDD panel note appended to the final report ------------------------
    "kedd.panel_note": {"tr": "🔔 Hepsi KEDD panelinde bildirimlerde.",
                        "en": "🔔 It's all in the KEDD panel notifications."},
    # -- approval.py: CRM card lines ------------------------------------------------
    "card.new_customer": {"tr": "👤 Yeni müşteri: {name}{tags}", "en": "👤 New customer: {name}{tags}"},
    "card.new_customer_tags": {"tr": " (etiket: {tags})", "en": " (tags: {tags})"},
    "card.customer_updated": {"tr": "🏷️ {name}: {parts}", "en": "🏷️ {name}: {parts}"},
    "card.tag_added": {"tr": "+etiket {tags}", "en": "+tag {tags}"},
    "card.tag_removed": {"tr": "-etiket {tags}", "en": "-tag {tags}"},
    "card.note_added": {"tr": "not eklendi", "en": "note added"},
    "card.renamed": {"tr": "yeni ad: {name}", "en": "new name: {name}"},
    "card.new_lead": {"tr": "🧲 Yeni lead: {name}{value}", "en": "🧲 New lead: {name}{value}"},
    "card.lead_value": {"tr": " — tahmini {amount} ₺", "en": " — est. {amount} ₺"},
    "card.lead_stage": {"tr": "📈 {name}: {prev} → {new}", "en": "📈 {name}: {prev} → {new}"},
    "card.stage.new": {"tr": "yeni", "en": "new"},
    "card.stage.contacted": {"tr": "temasta", "en": "contacted"},
    "card.stage.qualified": {"tr": "nitelikli", "en": "qualified"},
    "card.stage.proposal": {"tr": "teklif", "en": "proposal"},
    "card.stage.won": {"tr": "kazanıldı", "en": "won"},
    "card.stage.lost": {"tr": "kaybedildi", "en": "lost"},
    "card.lead_note": {"tr": "📝 Not: {note}", "en": "📝 Note: {note}"},
    "card.task": {"tr": "⏰ Görev: {title} — {when}", "en": "⏰ Task: {title} — {when}"},
    "card.task_done": {"tr": "✅ Görev tamamlandı: {title}", "en": "✅ Task completed: {title}"},
    "card.consent_note": {"tr": "🔕 Yeni müşteriye onayı olmadığı için kampanya gönderilemez",
                          "en": "🔕 The new customer has no marketing consent, so no campaign can be sent"},
    # -- executor progress lines: CRM ------------------------------------------------
    "progress.new_customer": {"tr": "✅ {i}/{n} Yeni müşteri: {name}{tags}",
                              "en": "✅ {i}/{n} New customer: {name}{tags}"},
    "progress.customer_updated": {"tr": "✅ {i}/{n} {name}: {parts}",
                                  "en": "✅ {i}/{n} {name}: {parts}"},
    "progress.new_lead": {"tr": "✅ {i}/{n} Yeni lead: {name}{value}",
                          "en": "✅ {i}/{n} New lead: {name}{value}"},
    "progress.lead_stage": {"tr": "✅ {i}/{n} Lead: {prev} → {new}",
                            "en": "✅ {i}/{n} Lead: {prev} → {new}"},
    "progress.lead_note": {"tr": "✅ {i}/{n} Not: {note}", "en": "✅ {i}/{n} Note: {note}"},
    "progress.task": {"tr": "✅ {i}/{n} Görev: {title} — {when}",
                      "en": "✅ {i}/{n} Task: {title} — {when}"},
    "progress.task_done": {"tr": "✅ {i}/{n} Görev tamamlandı", "en": "✅ {i}/{n} Task completed"},
    "progress.fail.create_customer": {"tr": "Müşteri oluşturulamadı",
                                        "en": "Couldn't create the customer"},
    "progress.fail.update_customer": {"tr": "Müşteri güncellenemedi",
                                        "en": "Couldn't update the customer"},
    "progress.fail.create_lead": {"tr": "Lead oluşturulamadı", "en": "Couldn't create the lead"},
    "progress.fail.set_lead_stage": {"tr": "Aşama güncellenemedi", "en": "Couldn't update the stage"},
    "progress.fail.add_lead_note": {"tr": "Not eklenemedi", "en": "Couldn't add the note"},
    "progress.fail.create_task": {"tr": "Görev oluşturulamadı", "en": "Couldn't create the task"},
    "progress.fail.complete_task": {"tr": "Görev tamamlanamadı", "en": "Couldn't complete the task"},
    "step.create_customer": {"tr": "Müşteri oluşturuldu", "en": "Customer created"},
    "step.update_customer": {"tr": "Müşteri güncellendi", "en": "Customer updated"},
    "step.create_lead": {"tr": "Lead oluşturuldu", "en": "Lead created"},
    "step.set_lead_stage": {"tr": "Lead aşaması güncellendi", "en": "Lead stage updated"},
    "step.add_lead_note": {"tr": "Lead notu eklendi", "en": "Lead note added"},
    "step.create_task": {"tr": "Görev oluşturuldu", "en": "Task created"},
    "step.complete_task": {"tr": "Görev tamamlandı", "en": "Task completed"},
    # -- approval.py: money + content card lines -----------------------------------------
    "card.appt_done": {"tr": "✔️ Randevu tamamlandı: {who} — {svc}{paid}",
                       "en": "✔️ Appointment completed: {who} — {svc}{paid}"},
    "card.appt_paid": {"tr": ", {amount} ₺{method}", "en": ", {amount} ₺{method}"},
    "card.method.cash": {"tr": "nakit", "en": "cash"},
    "card.method.card": {"tr": "kart", "en": "card"},
    "card.method.transfer": {"tr": "havale", "en": "transfer"},
    "card.method.other": {"tr": "diğer", "en": "other"},
    "card.quote_draft": {"tr": "📄 Teklif taslağı: {who} — {rows} = {total} ₺ (gönderilmez, taslak)",
                         "en": "📄 Quote draft: {who} — {rows} = {total} ₺ (not sent, draft)"},
    "card.quote_more": {"tr": " +{k} {unit}", "en": " +{k} {unit}"},
    "card.chatbot_teach": {"tr": "🧠 Chatbot öğrenecek: “{q}” → “{a}”",
                           "en": "🧠 Chatbot will learn: “{q}” → “{a}”"},
    # -- executor progress lines: money + content ----------------------------------------
    "progress.appt_done": {"tr": "✅ {i}/{n} Randevu tamamlandı{paid}",
                           "en": "✅ {i}/{n} Appointment completed{paid}"},
    "progress.quote": {"tr": "✅ {i}/{n} Teklif taslağı: {total} ₺",
                       "en": "✅ {i}/{n} Quote draft: {total} ₺"},
    "progress.chatbot": {"tr": "✅ {i}/{n} Chatbot öğrendi: “{q}”",
                         "en": "✅ {i}/{n} Chatbot learned: “{q}”"},
    "progress.fail.complete_appointment": {"tr": "Randevu tamamlanamadı",
                                           "en": "Couldn't complete the appointment"},
    "progress.fail.create_quote_draft": {"tr": "Teklif hazırlanamadı",
                                         "en": "Couldn't draft the quote"},
    "progress.fail.teach_chatbot": {"tr": "Chatbot öğrenemedi",
                                    "en": "Couldn't teach the chatbot"},
    "step.complete_appointment": {"tr": "Randevu tamamlandı", "en": "Appointment completed"},
    "step.create_quote_draft": {"tr": "Teklif taslağı hazırlandı", "en": "Quote drafted"},
    "step.teach_chatbot": {"tr": "Chatbot'a öğretildi", "en": "Taught the chatbot"},
    # -- approval.py: inbox card lines ----------------------------------------------------
    "card.inbox_head": {"tr": "💬 {who}: “{last}”", "en": "💬 {who}: “{last}”"},
    "card.inbox_reply": {"tr": "   ➜ Cevap: “{text}” (müşteriye gider)",
                         "en": "   ➜ Reply: “{text}” (goes to the customer)"},
    # -- executor progress lines: inbox ---------------------------------------------------
    "progress.reply": {"tr": "✅ {i}/{n} Cevap gitti: {customer}",
                       "en": "✅ {i}/{n} Reply sent: {customer}"},
    "progress.reply_bare": {"tr": "✅ {i}/{n} Cevap gitti", "en": "✅ {i}/{n} Reply sent"},
    "progress.fail.reply_to_conversation": {"tr": "Cevap gönderilemedi",
                                            "en": "Couldn't send the reply"},
    "step.reply_to_conversation": {"tr": "Cevap gönderildi", "en": "Reply sent"},
}


def t(key: str, lang: str = TR, default: str | None = None) -> str:
    """Fixed owner-facing string `key` in `lang`. Unknown languages fall back to Turkish."""
    entry = STRINGS.get(key)
    if entry is None:
        if default is not None:
            return default
        raise KeyError(key)
    return entry.get(lang) or entry[TR]


# --------------------------------------------------------------------------- #
# Execution progress lines. Rendered from the step record's facts only (action
# type, numbers, names, dates) — never model prose — so they are safe to show
# live on every channel. render_progress returns "" for events the owner need
# not see (skipped steps are already covered by the failed line's suffix).
# --------------------------------------------------------------------------- #

_TR_MONTHS = ("", "Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")
_EN_MONTHS = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_TR_TIME_SUFFIX = {"0": "'da", "1": "'de", "2": "'de", "3": "'te", "4": "'de", "5": "'te",
                   "6": "'da", "7": "'de", "8": "'de", "9": "'da"}  # "10:00'da", "14:05'te"
_TR_WEEKDAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
_EN_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WS_RE = re.compile(r"\s+")
_PHONE_RE = re.compile(r"\d{10,}")
_NAME_CHARS = 60
_MSG_CHARS = 100


def _clean(text: Any, max_chars: int) -> str:
    """One display-safe line: whitespace collapsed, long digit runs masked (never a phone
    number in the UI), WhatsApp markup stripped, truncated."""
    s = _WS_RE.sub(" ", str(text or "")).strip()
    s = _PHONE_RE.sub("‹tel›", s).replace("*", "").replace("_", "").replace("~", "")
    return s if len(s) <= max_chars else s[:max_chars - 1].rstrip() + "…"


def _when(iso: Any) -> str:
    """2026-10-31T15:00 → 31.10 15:00 (unparseable values are shown as-is)."""
    s = str(iso or "?")
    return f"{s[8:10]}.{s[5:7]} {s[11:16]}" if len(s) >= 16 and s[10] == "T" else s


def _day_month(iso: Any, lang: str) -> str:
    """2026-11-01 → "1 Kas" / "Nov 1" (unparseable values are shown as-is)."""
    try:
        _y, m, d = str(iso)[:10].split("-")
        mon = (_TR_MONTHS if lang != EN else _EN_MONTHS)[int(m)]
        day = int(d)
    except (ValueError, IndexError):
        return str(iso)
    return f"{day} {mon}" if lang != EN else f"{mon} {day}"


def _date_range(starts: Any, ends: Any, lang: str) -> str:
    """"2026-11-01".."2026-11-02" → "1–2 Kas" / "Nov 1–2"; other shapes stay explicit."""
    s, e = str(starts or ""), str(ends or "")
    if len(s) >= 10 and len(e) >= 10 and s[:7] == e[:7]:
        try:
            d1, d2, m = int(s[8:10]), int(e[8:10]), int(s[5:7])
            mon = (_TR_MONTHS if lang != EN else _EN_MONTHS)[m]
            return f"{d1}–{d2} {mon}" if lang != EN else f"{mon} {d1}–{d2}"
        except (ValueError, IndexError):
            pass
    return f"{_day_month(s, lang)}–{_day_month(e, lang)}"


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _relative_day(iso: Any, today: str | None, lang: str) -> str:
    """ISO date → "bugün"/"yarın" (or "today"/"tomorrow") when it matches `today`, else "5 Kas"."""
    s = str(iso or "")[:10]
    if today:
        try:
            d, base = date.fromisoformat(s), date.fromisoformat(today[:10])
            if d == base:
                return t("progress.today", lang)
            if d == base + timedelta(days=1):
                return t("progress.tomorrow", lang)
        except ValueError:
            pass
    return _day_month(s, lang)


def format_task_due(iso: Any, lang: str = TR) -> str:
    """Task due date for cards and progress: "2026-10-31T10:00" → "Cuma 10:00" / "Friday 10:00";
    date-only values render as "31 Eki" / "Oct 31". Unparseable values are shown as-is."""
    s = str(iso or "")
    if len(s) >= 16 and s[10] in "T ":
        try:
            weekday = (_TR_WEEKDAYS if lang != EN else _EN_WEEKDAYS)[date.fromisoformat(s[:10]).weekday()]
            return f"{weekday} {s[11:16]}"
        except ValueError:
            return s
    return _day_month(s, lang) if s else "?"


def _failed_line(i: int, n: int, step: dict[str, Any], lang: str) -> str:
    verb = t(f"progress.fail.{step.get('action')}", lang, str(step.get("action")))
    raw = step.get("result")
    s = raw if isinstance(raw, str) else str(raw or "")
    head, sep, tail = s.partition(": ")
    # Strip the "ValueError: " style prefix the executor prepends; keep anything else verbatim.
    msg = _clean(tail if sep and re.fullmatch(r"[A-Za-z_][\w.]*", head) else s, _MSG_CHARS)
    msg_part = t("progress.failed_msg", lang).format(msg=msg) if msg else ""
    stop = t("progress.failed_stop", lang) if i < n else ""
    return t("progress.failed", lang).format(i=i, n=n, verb=verb, msg=msg_part, stop=stop)


def render_progress(event: str, i: int, n: int, step: dict[str, Any] | None,
                    lang: str = TR, today: str | None = None) -> str:
    """One owner-facing progress line for an executor event, or "" when silent.

    `today` is an ISO date ("YYYY-MM-DD") used only to say "bugün/yarın".
    """
    if event == "start":
        unit = t("progress.step_one" if n == 1 else "progress.step_many", lang)
        return t("progress.start", lang).format(n=n, unit=unit)
    if event == "failed":
        return _failed_line(i, n, step or {}, lang)
    if event != "ok" or not step:
        return ""
    r = step.get("result") if isinstance(step.get("result"), dict) else {}
    head = {"i": i, "n": n}
    kind = step.get("action")
    if kind == "add_catalog_item":
        key = "progress.service_added" if r.get("type") == "service" else "progress.item_added"
        return t(key, lang).format(**head, name=_clean(r.get("name"), _NAME_CHARS),
                                   price=_money(r.get("price")))
    if kind == "create_discount":
        return t("progress.discount", lang).format(
            **head, p=r.get("percent"), dates=_date_range(r.get("starts"), r.get("ends"), lang))
    if kind == "generate_visual":
        return t("progress.visual", lang).format(**head)
    if kind == "send_campaign":
        k = r.get("recipients")
        return t("progress.campaign", lang).format(**head, k="?" if k is None else k)
    if kind == "publish_social_post":
        plats = ", ".join(str(p)[:1].upper() + str(p)[1:] for p in (r.get("platforms") or [])) or "?"
        sched = str(r.get("scheduled_for") or "")
        hm = sched[11:16] if len(sched) >= 16 and sched[10] in "T " else ""
        if not sched:
            return t("progress.post_now", lang).format(**head, plats=plats)
        suffix = _TR_TIME_SUFFIX.get(hm[-1:], "") if lang != EN else ""
        line = t("progress.post_scheduled", lang).format(
            **head, plats=plats, day=_relative_day(sched, today, lang), hm=hm, suffix=suffix)
        return " ".join(line.split())
    if kind == "create_appointment":
        return t("progress.appt_created", lang).format(**head, when=_when(r.get("starts")))
    if kind == "reschedule_appointment":
        return t("progress.appt_moved", lang).format(**head, when=_when(r.get("starts")))
    if kind == "cancel_appointment":
        return t("progress.appt_cancelled", lang).format(**head)
    if kind == "apply_bulk_price":
        k = r.get("updated")
        unit = t("card.item_one" if k == 1 else "card.item_many", lang)
        return t("progress.bulk", lang).format(**head, k="?" if k is None else k, unit=unit)
    if kind == "adjust_loyalty":
        try:
            delta = f"{int(r.get('delta')):+d}"  # type: ignore[arg-type]
        except (TypeError, ValueError):
            delta = str(r.get("delta"))
        total = r.get("points")
        return t("progress.loyalty", lang).format(**head, delta=delta,
                                                 total="?" if total is None else total)
    if kind == "ads_action":
        return t("progress.ad", lang).format(**head)
    if kind == "create_customer":
        tags = ", ".join(str(x) for x in (r.get("tags") or []))
        suffix = t("card.new_customer_tags", lang).format(tags=_clean(tags, _NAME_CHARS)) if tags else ""
        return t("progress.new_customer", lang).format(
            **head, name=_clean(r.get("name"), _NAME_CHARS), tags=suffix)
    if kind == "update_customer":
        parts = update_parts(r.get("added_tags"), r.get("removed_tags"), bool(r.get("note_added")),
                             bool(r.get("renamed")), str(r.get("name") or "?"), lang)
        return t("progress.customer_updated", lang).format(
            **head, name=_clean(r.get("name"), _NAME_CHARS), parts=", ".join(parts) or "—")
    if kind == "create_lead":
        value = _money(r.get("value")) if r.get("value") is not None else ""
        suffix = t("card.lead_value", lang).format(amount=value) if value else ""
        return t("progress.new_lead", lang).format(
            **head, name=_clean(r.get("name"), _NAME_CHARS), value=suffix)
    if kind == "set_lead_stage":
        prev = t(f"card.stage.{r.get('prev_stage')}", lang, str(r.get("prev_stage")))
        new = t(f"card.stage.{r.get('stage')}", lang, str(r.get("stage")))
        return t("progress.lead_stage", lang).format(**head, prev=prev, new=new)
    if kind == "add_lead_note":
        return t("progress.lead_note", lang).format(**head, note=_clean(r.get("note"), 80))
    if kind == "create_task":
        return t("progress.task", lang).format(**head, title=_clean(r.get("title"), _NAME_CHARS),
                                               when=format_task_due(r.get("due_date"), lang))
    if kind == "complete_task":
        return t("progress.task_done", lang).format(**head)
    if kind == "complete_appointment":
        paid = ""
        if r.get("amount") is not None:
            method = t(f"card.method.{r.get('method')}", lang, "") if r.get("method") else ""
            paid = t("card.appt_paid", lang).format(amount=_money(r.get("amount")),
                                                   method=f" {method}" if method else "")
        return t("progress.appt_done", lang).format(**head, paid=paid)
    if kind == "create_quote_draft":
        return t("progress.quote", lang).format(**head, total=_money(r.get("total")))
    if kind == "teach_chatbot":
        return t("progress.chatbot", lang).format(**head, q=_clean(r.get("question"), 80))
    if kind == "reply_to_conversation":
        if r.get("customer"):
            return t("progress.reply", lang).format(
                **head, customer=_clean(r.get("customer"), _NAME_CHARS))
        return t("progress.reply_bare", lang).format(**head)
    return f"✅ {i}/{n} {kind}"


def update_parts(added: Any, removed: Any, noted: bool, renamed: bool, name: str,
                 lang: str) -> list[str]:
    """Shared update-customer fragments for cards (from the action) and progress (from the record)."""
    parts = []
    if added:
        parts.append(t("card.tag_added", lang).format(tags=", ".join(str(x) for x in added)))
    if removed:
        parts.append(t("card.tag_removed", lang).format(tags=", ".join(str(x) for x in removed)))
    if noted:
        parts.append(t("card.note_added", lang))
    if renamed:
        parts.append(t("card.renamed", lang).format(name=name))
    return parts


# --------------------------------------------------------------------------- #
# Language detection. Deterministic, no model call: Turkish-specific letters plus
# common stop/utility words per language. Ties (including emoji-only or bare
# control words) keep the previous value. Approval words (evet/yes/…) are
# deliberately in neither list — they say nothing about the owner's language.
# --------------------------------------------------------------------------- #

_TR_LETTERS = frozenset("çÇğĞıİöÖşŞüÜ")

_TR_WORDS = frozenset("""
bir ve veya bu şu bunlar şunlar bunu şunu bunun şunun bunların şunların onlar onların onlara onları
onu biz bize bizi bizim siz size sizi sizin ben beni bana benim sen seni sana senin kendi hep hiç hiçbir
her herkes şey kimse kimi kime kimin kim ne neler neyi neye nasıl neden niçin niye kaç kaçıncı hangi
hangisi nerede nereden nereye zaman da de dahi bile ki mi mı mu mü mısın misin musun müsün için
ile birlikte gibi kadar göre daha çok pek az fazla en hem ya ama fakat ancak çünkü eğer ise değil
yok var ekle ekleyin ver verin yap yapın gönder gönderin gonder gonderin hazırla hazırlayın hazirla
göster gösterin goster gosterin listele listeleyin söyle söyleyin soyle yaz yazın al alın bak bakın
bul bulun değiştir değiştirin degistir iptal kaydet kaydedin sil silin başlat başlatın baslat durdur
durdurun duraklat artır arttır azalt azaltın güncelle güncelleyin guncelle taşı taşıyın tasi
oluştur oluşturun olustur kur kurun planla planlayın sor sorun anlat anlatın
özetle özetleyin ozetle getir
getirin kaldır kaldırın kaldir düzelt düzeltin duzelt kontrol et incele inceleyin onayla onaylıyorum
onayliyorum reddet reddedin vazgeç vazgec hatırla hatırlat bekle bekleyin dene deneyin tekrarla
tekrarlayın yenile yenileyin kapat kapatın aç çalış çalışın calis dur devam bitir tamamla
tamamlayın paylaş paylaşın paylas yayınla yayınlayın yayinla duyur duyurun
tanıt tanıtın tanit çağır
çağırın cagir davet ilet iletin cevap cevapla cevaplayın yanıt yanıtla
ara arayın öner önerin tavsiye seç
seçin sec ayarla ayarlayın düzenle düzenleyin duzenle sırala filtrele randevu randevular randevusu
randevuma randevum randevuyu randevusunu randevudan randevuya indirim indirimi indirimler indirimli
indirimde kampanya kampanyası kampanyalar kampanyayı müşteri müşteriler müşterilere müşteriye
müşteriyi müşterinin fiyat fiyatı fiyatlar fiyatları fiyatını fiyatlara ürün ürünler ürünleri
ürünü ürünün ürünlere katalog kataloğu kataloğa katalogda mesaj mesajı mesajlar mesajları
mesajlara fotoğraf fotoğrafı fotoğraflar fotoğrafları resim resimler görsel görseli görseller satış
satışlar satışları ciro cirosu ciroyu kazanç kar zarar gelir gider
kasa stok malzeme kumaş beden numara renk
marka koleksiyon sezon yeni yeniler eski eskiler tüm bütün bazı birkaç birçok bugün bugünkü yarın
yarınki dün dünkü hafta haftaya sonu son sabah akşam öğle öğleden gece saat dakika gün günü
günler günlük ay yıl pazartesi salı çarşamba perşembe cuma cumartesi
pazar ocak şubat mart nisan mayıs
haziran temmuz ağustos eylül ekim kasım aralık boş boşluk boşluklar dolu müsait uygun lira kuruş
tl adet tanesi yüzde oran uzun süredir gelenler gelmeyenler gelen gelmeyen sadık müdavim ziyaret
ziyaretçi devamlı hizmet hizmetler hizmeti servis servisler servisi bakım bakımı bakımlar kesim
kesimi boya saç makyaj manikür pedikür cilt masaj kuaför berber butik salon mağaza dükkan işletme
firma şirket oldu olur olsun olacak tamam peki tabii merhaba selam selamlar günaydın iyi akşamlar
lütfen teşekkür teşekkürler sağol kolay gelsin özür dilerim hayırlı işler iş rapor özet liste
tablo grafik hesap borç taksit zam promosyon çekiliş hediye puan
sadakat kart üye üyelik kayıt rezervasyon
sıra bekleyen bekleyenler soru sorun hata yanlış doğru eksik yeterli hazır mümkün gerekli lazım
açık kapalı personel çalışan eleman çırak ilk tek çift yarım tam su sali carsamba persembe subat
mayis agustos eylul kasim aralik kurus yuzde sadik mudavim suredir magaz
""".split() + """
yarin bugun gunaydin simdi cok degil urun urunler urunleri musteri musteriler musterilere musteriye
ogleden ogle aksam soyleyin boyle oyle bos bosluk bosluklar ucret gunluk gunler yarinki bugunku
dukkan isletme sagol lutfen tesekkur suan
""".split())

_EN_WORDS = frozenset("""
the a an and or but if then because as of at by for with about into through during before after above
below to from up down in out on off over under again further once here there when where why how all
each every both few more most other another some such only own same so than too very can will just
should now is are was were be been being have has had having do does did doing would could ought shall
may might must i you she it we they my mine your yours his its our ours their theirs this that these
those who whom which what please add new show list send make create give tell get set update cancel
book move change check prepare write remove delete start stop pause resume increase decrease price
prices discount sale campaign message messages customer customers photo photos image images product
products collection catalog catalogue appointment appointments slot slots morning today tomorrow
yesterday weekend week free empty available loyal regular regulars visit visited visits haven hasn
hadn didn don won wouldn couldn shouldn isn aren wasn weren hello hi hey thanks thank good daily
weekly monthly season holiday holidays long time times ago since until while between within without
per every other same next last first second third one two three offer code visual publish share
scheduled schedule calendar reminder confirm confirmed approved done ready question questions
answer help idea suggest recommend want need like sorry welcome birthday loyalty points budget spend
spent click clicks impression impressions result results performance suggestion suggestions active
stock inventory delivery gift size sizes small medium large red blue black white green yellow pink
purple orange brown grey gray beige navy leather cotton silk wool linen old minute minutes hour hours
many much lot several either neither nor yet still already even ever never always usually often
sometimes maybe sure right thing things something anything nothing everything someone anyone
everyone nobody else kind rest whole special revenue profit treatment coloring color hair silk scarf
scarves bag bags dress dresses shoe shoes soon open booked full year month months day days
""".split())

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_lang(text: str, prev: str = TR) -> str:
    """Guess "tr" or "en" for owner text. Ties keep `prev` (default "tr")."""
    lowered = text.lower().replace("i\u0307", "i")  # İ lowercases to i + combining dot
    words = _WORD_RE.findall(lowered)
    tr = sum(1 for w in words if w in _TR_WORDS)
    if any(c in _TR_LETTERS for c in text):
        tr += 1
    en = sum(1 for w in words if w in _EN_WORDS)
    if tr > en:
        return TR
    if en > tr:
        return EN
    return prev if prev in (TR, EN) else TR
