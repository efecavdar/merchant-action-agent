"""Owner language: fixed strings (Turkish/English) + deterministic language detection.

The owner may write Turkish or English; every fixed owner-facing string is looked up here by key
and rendered in `Session.lang`. Customer-facing text (campaign messages, captions, catalog
names/descriptions) is always Turkish and never goes through this module.
"""

from __future__ import annotations

import re

TR = "tr"
EN = "en"

STRINGS: dict[str, dict[str, str]] = {
    # -- core.py: greeting, commands, status -----------------------------------
    "help": {
        "tr": ("Merhaba, ben Çırak 👋 Sesli not ya da yazıyla ne istediğini söyle, istersen önce "
               "ürün fotoğrafı at.\n"
               "Örn: \"Bunları ekle, tanesi 850 lira, hafta sonu %15 indirim, uzun süredir "
               "gelmeyenlere haber ver.\"\n"
               "Hiçbir şey senin *evet*'in olmadan yapılmaz.\n"
               "Demo: /butik, /salon veya /kedd ile işletme değiştir, /sifirla ile baştan başla."),
        "en": ("Hi, I'm Çırak 👋 Tell me what you need by voice note or text, or send product "
               "photos first.\n"
               "E.g.: \"Add these, 850 lira each, 15% off this weekend, tell customers who haven't "
               "visited in a while.\"\n"
               "Nothing happens without your *yes*.\n"
               "Demo: switch business with /butik, /salon or /kedd, start over with /sifirla."),
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
