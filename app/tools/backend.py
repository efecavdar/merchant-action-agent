"""Business backend interface, shaped after KEDD's data model.

Two implementations:

* MockBackend — a self-contained, in-memory store seeded with SYNTHETIC data
  (fictional businesses, fictional customers). This is what judges run. Nothing here
  touches a real system. Two seeds: a boutique (products) and a hair salon (services).
* KeddBackend — a thin HTTP adapter for our commercial platform (KEDD). It talks to a
  small, signed "agent gateway" on the KEDD side. The KEDD codebase itself is NOT part of
  this repository; only this adapter was written during the hackathon.

Model notes (mirroring KEDD):
  - products and services are ONE entity (`catalog_items`, `type` = product | service)
  - there is no coupon entity; a discount is a `sale_price` window on catalog items
  - customers carry stats (last_visit, visit_count, total_spent), tags and a marketing consent flag
  - customer phone numbers never leave the backend; segments are returned as counts + samples
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import random
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Protocol

from ..config import settings


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

ITEM_TYPES = ("product", "service")


@dataclass
class CatalogItem:
    id: str
    type: str                     # "product" | "service"
    name: str
    price: float
    currency: str = "TRY"
    category: str = ""
    description: str = ""
    image_path: str = ""
    duration_minutes: int | None = None   # services only
    sale_price: float | None = None       # KEDD-style discount: a sale price window
    sale_starts: str | None = None
    sale_ends: str | None = None
    active: bool = True
    added: str = ""                     # ISO date the item was added ("" = pre-existing seed)


@dataclass
class Customer:
    id: str
    name: str
    phone: str                # synthetic; never a real number; never returned by tools
    last_visit: str           # ISO date
    visit_count: int
    total_spent: float
    tags: list[str] = field(default_factory=list)
    marketing_consent: bool = True
    created: str = ""                   # ISO date the customer signed up ("" = old customer)
    loyalty_points: int = 0

    def loyalty_status(self, today: date) -> str:
        days = (today - date.fromisoformat(self.last_visit)).days
        return "active" if days < 45 else ("at_risk" if days < 120 else "lost")


@dataclass
class Appointment:
    id: str
    customer_id: str
    item_id: str              # a service
    staff: str
    starts: str               # ISO datetime
    ends: str
    status: str = "confirmed"


@dataclass
class Discount:
    id: str
    name: str
    percent: int
    item_ids: list[str]
    starts: str
    ends: str


@dataclass
class Campaign:
    id: str
    name: str
    segment_id: str
    message: str
    visual_path: str
    recipients: int
    template: str = ""
    variables: dict[str, str] = field(default_factory=dict)
    status: str = "scheduled"
    # Engagement is SIMULATED in mock mode and clearly labelled as such in reports.
    replies: int = 0
    conversions: int = 0
    day: str = ""                       # ISO date the campaign was sent


@dataclass
class SocialPost:
    id: str
    caption: str
    platforms: list[str]
    image_path: str
    publish_at: str | None = None   # ISO datetime when scheduled, else published now
    status: str = "published"       # "published" | "scheduled" | "preview" (dry run)


@dataclass
class RevenueEntry:
    id: str
    day: str                        # ISO date of the sale
    category: str
    amount: float
    item_name: str = ""


@dataclass
class Lead:
    id: str
    name: str                       # display name only, no contact details on purpose
    day: str                        # ISO date the lead came in
    source: str = ""
    interest: str = ""


@dataclass
class InboxMessage:
    id: str
    day: str                        # ISO date received
    customer: str                   # display name only, never a phone number
    preview: str                    # synthetic snippet
    age_minutes: int = 0


# --------------------------------------------------------------------------- #
# Approved WhatsApp campaign templates
# --------------------------------------------------------------------------- #
# A business may only start a conversation with an approved template. {{1}} is always the
# customer name and is filled per recipient by the gateway (KEDD) or the mock; the model only
# supplies the remaining variables as short strings. Bodies carry no numbers so they never
# force offer text into a plan — numbers arrive via the variables and are validated there.

_CAMPAIGN_TEMPLATES: list[dict[str, Any]] = [
    {"name": "marketing_reengagement_promo_tr",
     "body": "Merhaba {{1}}! Seni özledik 💛 {{3}} — kasada {{2}} kodunu söylemen yeterli.",
     "variables": [{"number": 1, "name": "name"}, {"number": 2, "name": "code"},
                   {"number": 3, "name": "advantage"}]},
    {"name": "marketing_engagement_thankyou_promo_tr",
     "body": "Teşekkürler {{1}}! Sana özel bir hediyemiz var: {{3}} 🎁 Kodun: {{2}}",
     "variables": [{"number": 1, "name": "name"}, {"number": 2, "name": "code"},
                   {"number": 3, "name": "discount"}]},
    {"name": "marketing_welcome_offer_friendly_tr",
     "body": "Aramıza hoş geldin {{1}}! İlk siparişinde seni küçük bir sürpriz bekliyor 🎉",
     "variables": [{"number": 1, "name": "name"}]},
]


def render_campaign_template(body: str, values: dict[str, str]) -> str:
    """Fill {{n}} placeholders from `values` (keys "1", "2", …). Unknown placeholders stay as-is."""
    out = body
    for key, val in values.items():
        out = out.replace("{{" + str(key) + "}}", str(val))
    return out


@dataclass
class AdCampaign:
    id: str
    name: str
    platform: str                   # "Instagram" | "Meta"
    status: str                     # "active" | "paused"
    daily_budget: float
    spend: float                    # last 7 days, TRY (mock base figures; scaled by `days`)
    impressions: int                # last 7 days
    clicks: int                     # last 7 days
    results: int                    # last 7 days (messages, bookings…)


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #

class Backend(Protocol):
    name: str

    # read (available to the model as tools)
    def store_profile(self) -> dict[str, Any]: ...
    def list_catalog(self, item_type: str | None = None) -> list[dict[str, Any]]: ...
    def find_customers(self, inactive_days: int | None = None, min_visits: int = 1,
                       tags: list[str] | None = None) -> dict[str, Any]: ...
    def get_schedule(self, day: str) -> dict[str, Any]: ...
    def find_customer(self, name: str) -> list[dict[str, Any]]: ...
    def daily_summary(self, day: str) -> dict[str, Any]: ...
    def find_appointments(self, customer_name: str | None = None,
                          day: str | None = None) -> list[dict[str, Any]]: ...
    def preview_bulk_price(self, item_type: str | None = None, category: str | None = None,
                           item_ids: list[str] | None = None, mode: str | None = None,
                           value: float | None = None) -> dict[str, Any]: ...
    def ads_performance(self, days: int = 7) -> dict[str, Any]: ...
    def ads_suggestions(self) -> dict[str, Any]: ...
    def campaign_templates(self) -> list[dict[str, Any]]: ...
    def preview_campaign(self, segment_id: str, template: str,
                         variables: dict[str, str] | None = None) -> dict[str, Any]: ...
    def find_loyalty_member(self, name: str) -> dict[str, Any]: ...

    # write (only the executor calls these, after human approval)
    def add_catalog_item(self, item_type: str, name: str, price: float, category: str,
                         description: str, image_path: str = "",
                         duration_minutes: int | None = None) -> dict[str, Any]: ...
    def create_discount(self, name: str, percent: int, item_ids: list[str],
                        starts: str, ends: str) -> dict[str, Any]: ...
    def send_campaign(self, name: str, segment_id: str, message: str = "",
                      visual_path: str = "", template: str | None = None,
                      variables: dict[str, str] | None = None) -> dict[str, Any]: ...
    def create_appointment(self, customer_id: str, item_id: str, staff: str,
                           starts: str) -> dict[str, Any]: ...
    def reschedule_appointment(self, appointment_id: str, starts: str,
                               staff: str | None = None) -> dict[str, Any]: ...
    def cancel_appointment(self, appointment_id: str, reason: str = "") -> dict[str, Any]: ...
    def apply_bulk_price(self, preview_id: str) -> dict[str, Any]: ...
    def adjust_loyalty(self, customer_id: str, points: int, reason: str) -> dict[str, Any]: ...
    def ads_action(self, campaign_id: str, action: str,
                   delta_pct: int | None = None) -> dict[str, Any]: ...
    def publish_social_post(self, caption: str, image_path: str, platforms: list[str],
                            publish_at: str | None = None,
                            dry_run: bool = False) -> dict[str, Any]: ...
    def report(self) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- #
# Mock backend with synthetic data
# --------------------------------------------------------------------------- #

_FIRST = ["Ayşe", "Elif", "Zeynep", "Merve", "Selin", "Büşra", "Derya", "Ceren", "Gamze", "Ece",
          "Deniz", "Nazlı", "Pelin", "Sibel", "Tuğba", "Yasemin", "İrem", "Hande", "Melis", "Aslı"]
_LAST = ["Y.", "K.", "A.", "D.", "S.", "Ç.", "Ö.", "T.", "B.", "E."]

_SEEDS: dict[str, dict[str, Any]] = {
    "butik": {
        "profile": {
            "business_name": "Nar Butik",
            "city": "Bursa",
            "sector": "women's fashion boutique",
            "channels": ["WhatsApp", "Instagram"],
            "tone": "warm, friendly, informal Turkish (sen), short sentences, one emoji max",
            "currency": "TRY",
            "business_hours": {"open": "10:00", "close": "20:00"},
            "staff": [],
        },
        "items": [
            ("product", "Keten Gömlek", 650, "üst giyim", "Bej keten gömlek", None),
            ("product", "Deri Kemer", 420, "aksesuar", "Kahverengi deri kemer", None),
            ("product", "Midi Elbise", 1290, "elbise", "Siyah midi elbise", None),
        ],
        "tags": ["çanta", "ayakkabı", "aksesuar", "elbise"],
        "customers": 42,
        # (name, platform, status, daily_budget, spend, impressions, clicks, results) — 7-day figures
        "ads": [
            ("Yaz Elbiseleri", "Instagram", "active", 500, 3150, 42000, 1150, 88),
            ("Deri Çanta", "Instagram", "active", 400, 2650, 18000, 290, 9),
            ("Mağaza Bilinirliği", "Meta", "paused", 250, 900, 12000, 210, 14),
        ],
        "suggestions": [
            {"campaign_id": "ad2", "kind": "pause",
             "text": "Deri Çanta ~294 ₺/sonuç harcıyor; duraklat, bütçeyi Elbiseler'e kaydır."},
            {"campaign_id": "ad1", "kind": "increase_budget",
             "text": "Yaz Elbiseleri ~36 ₺/sonuç ile iyi; bütçeyi %15 artır."},
        ],
    },
    "salon": {
        "profile": {
            "business_name": "Studio Elif Kuaför",
            "city": "İzmir",
            "sector": "hair salon",
            "channels": ["WhatsApp", "Instagram"],
            "tone": "warm, friendly, informal Turkish (sen), short sentences, one emoji max",
            "currency": "TRY",
            "business_hours": {"open": "10:00", "close": "19:00"},
            "staff": ["Elif", "Deniz"],
        },
        "items": [
            ("service", "Saç Kesimi", 450, "kesim", "Yıkama dahil kadın saç kesimi", 45),
            ("service", "Fön", 300, "şekillendirme", "Yıkama ve fön", 30),
            ("service", "Saç Boyama", 1800, "boya", "Dip boya veya tam boya, bakım dahil", 120),
            ("service", "Keratin Bakımı", 2500, "bakım", "Brezilya fönü / keratin bakımı", 90),
            ("service", "Manikür", 400, "el-ayak", "Kalıcı oje dahil manikür", 45),
        ],
        "tags": ["boya", "kesim", "bakım", "manikür"],
        "customers": 38,
        # (name, platform, status, daily_budget, spend, impressions, clicks, results) — 7-day figures
        "ads": [
            ("Saç Boyama", "Instagram", "active", 600, 3900, 38000, 980, 64),
            ("Keratin Bakımı", "Instagram", "active", 350, 2200, 15000, 240, 6),
            ("Gelin Sezonu", "Meta", "active", 300, 1500, 16000, 410, 31),
        ],
        "suggestions": [
            {"campaign_id": "ad2", "kind": "pause",
             "text": "Keratin Bakımı sonuç başına ~367 ₺ harcıyor; duraklatmayı düşün."},
            {"campaign_id": "ad3", "kind": "increase_budget",
             "text": "Gelin Sezonu sonuç başına ~48 ₺ ile verimli; bütçeyi %10 artır."},
        ],
    },
}


def _seed_customers(rng: random.Random, n: int, today: date, tags: list[str]) -> list[Customer]:
    out = []
    for i in range(n):
        days_ago = rng.choice([rng.randint(3, 40), rng.randint(45, 120), rng.randint(130, 300)])
        visits = rng.randint(1, 9)
        out.append(Customer(
            id=f"c{i+1:03d}",
            name=f"{rng.choice(_FIRST)} {rng.choice(_LAST)}",
            phone=f"+90 5XX XXX {i+1:02d} {rng.randint(10, 99)}",  # masked synthetic
            last_visit=(today - timedelta(days=days_ago)).isoformat(),
            visit_count=visits,
            total_spent=round(visits * rng.uniform(400, 1500), 2),
            tags=rng.sample(tags, k=rng.randint(1, 2)),
            marketing_consent=rng.random() < 0.85,
        ))
    return out


def _seed_appointments(rng: random.Random, today: date, customers: list[Customer],
                       services: list[CatalogItem], staff: list[str], days: int = 3) -> list[Appointment]:
    """A plausible agenda for the next few days with visible gaps."""
    out = []
    n = 0
    for d in range(days):
        day = today + timedelta(days=d)
        for s in staff:
            t = datetime.combine(day, dtime(10, 0))
            while t.hour < 17:
                if rng.random() < 0.55:
                    svc = rng.choice(services)
                    end = t + timedelta(minutes=svc.duration_minutes or 30)
                    n += 1
                    out.append(Appointment(f"a{n:03d}", rng.choice(customers).id, svc.id, s,
                                           t.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")))
                    t = end
                else:
                    t += timedelta(minutes=60)
    return out


_TR_FOLD = str.maketrans({"ı": "i", "I": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                          "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})


def _fold_name(s: str) -> str:
    """Lowercase + accent-stripped, so 'ayşe' matches 'Ayşe Y.' (dotless ı mapped before NFKD)."""
    ascii_only = unicodedata.normalize("NFKD", s.translate(_TR_FOLD)).encode("ascii", "ignore").decode()
    return ascii_only.lower()


_MESSAGE_PREVIEWS = (
    "Merhaba, cumartesi için yer var mı?",
    "Fiyat ne kadar?",
    "Kargo kaç günde gelir?",
    "Randevumu değiştirebilir miyim?",
    "İndirim ne zaman bitiyor?",
    "Bu ürün stokta var mı?",
    "Yarın uğrayabilir miyim?",
    "Ödeme nasıl oluyor?",
)
_LEAD_SOURCES = ("Instagram", "WhatsApp", "Tavsiye", "Google")
_SUMMARY_WINDOW_DAYS = 7


def _seed_summary(rng: random.Random, items_cfg: list, customers: list[Customer],
                  today: date, tags: list[str]) -> tuple[list[RevenueEntry], list[Lead], list[InboxMessage]]:
    """Seeded revenue / signup / lead / inbox data behind daily_summary (both businesses)."""
    revenue: list[RevenueEntry] = []
    leads: list[Lead] = []
    messages: list[InboxMessage] = []
    for back in range(_SUMMARY_WINDOW_DAYS - 1, -1, -1):
        day = (today - timedelta(days=back)).isoformat()
        for _ in range(rng.randint(2, 5)):
            _t, name, price, cat, _d, _dur = rng.choice(items_cfg)
            revenue.append(RevenueEntry(f"r{len(revenue) + 1:04d}", day, cat, float(price), name))
        for _ in range(rng.randint(0, 2)):
            leads.append(Lead(f"l{len(leads) + 1:03d}", f"{rng.choice(_FIRST)} {rng.choice(_LAST)}", day,
                              rng.choice(_LEAD_SOURCES), rng.choice(tags)))
        if back <= 1:
            for _ in range(rng.randint(1, 3)):
                messages.append(InboxMessage(f"m{len(messages) + 1:03d}", day, rng.choice(customers).name,
                                             rng.choice(_MESSAGE_PREVIEWS), rng.randint(5, 480)))
    for c in rng.sample(customers, k=min(5, len(customers))):
        c.created = (today - timedelta(days=rng.randint(0, 2))).isoformat()
    return revenue, leads, messages


class MockBackend:
    """In-memory KEDD-shaped backend. State can be persisted to data/store.json for the demo."""

    name = "mock"
    # Bump when the attributes of MockBackend change: sessions pickled with an older shape get re-seeded.
    SCHEMA = 5  # 5: social_posts (task 14)

    PREVIEW_TTL_S = 15 * 60

    def __init__(self, business: str | None = None, store_path: Path | None = None,
                 seed: int = 16, today: date | None = None):
        business = business or settings.mock_business
        if business not in _SEEDS:
            raise ValueError(f"unknown mock business {business!r}; choose from {sorted(_SEEDS)}")
        cfg = _SEEDS[business]
        self.business = business
        self.today = today or date.today()
        self.store_path = store_path or (settings.data_dir / "store.json")
        rng = random.Random(seed)
        self.profile = dict(cfg["profile"])
        self.items: list[CatalogItem] = [
            CatalogItem(f"i{k+1:03d}", t, n, float(p), "TRY", c, d, duration_minutes=dur)
            for k, (t, n, p, c, d, dur) in enumerate(cfg["items"])
        ]
        self.customers: list[Customer] = _seed_customers(rng, cfg["customers"], self.today, cfg["tags"])
        p_rng = random.Random(seed + 21)   # own stream: must not shift the customer/appointment seeds
        for c in self.customers:
            c.loyalty_points = p_rng.choice([0, 0, 0, 50, 100, 150, 200, 300, 500])
        self.ads: list[AdCampaign] = [
            AdCampaign(f"ad{k+1}", name, platform, status, float(budget), float(spend),
                       impressions, clicks, results)
            for k, (name, platform, status, budget, spend, impressions, clicks, results)
            in enumerate(cfg["ads"])
        ]
        self.ad_suggestions: list[dict[str, Any]] = [dict(s) for s in cfg["suggestions"]]
        self.previews: dict[str, dict[str, Any]] = {}
        services = [i for i in self.items if i.type == "service"]
        self.appointments: list[Appointment] = (
            _seed_appointments(rng, self.today, self.customers, services, self.profile["staff"])
            if services and self.profile["staff"] else []
        )
        self.discounts: list[Discount] = []
        self.campaigns: list[Campaign] = []
        self.social_posts: list[SocialPost] = []
        self.segments: dict[str, list[str]] = {}
        s_rng = random.Random(seed + 7)   # own stream: summary seeds must not shift the existing ones
        self.revenue_entries, self.leads, self.messages = _seed_summary(
            s_rng, cfg["items"], self.customers, self.today, cfg["tags"])
        self.appointment_changes: list[dict[str, Any]] = []
        self._rng = random.Random(seed + 1)
        self._counter = 100
        self._items_at_start = len(self.items)
        self._schema = self.SCHEMA

    # -- helpers ------------------------------------------------------------- #
    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"

    def _item(self, item_id: str) -> CatalogItem:
        for i in self.items:
            if i.id == item_id:
                return i
        raise ValueError(f"unknown catalog item {item_id}")

    def _customer(self, customer_id: str) -> Customer:
        for c in self.customers:
            if c.id == customer_id:
                return c
        raise ValueError(f"Müşteri bulunamadı: {customer_id}")

    def _appointment(self, appointment_id: str) -> Appointment:
        for a in self.appointments:
            if a.id == appointment_id:
                return a
        raise ValueError(f"Randevu bulunamadı: {appointment_id}")

    def _ad(self, campaign_id: str) -> AdCampaign:
        for c in self.ads:
            if c.id == campaign_id:
                return c
        raise ValueError(f"Reklam bulunamadı: {campaign_id}")

    @staticmethod
    def _parse_starts(starts: str) -> datetime:
        try:
            return datetime.fromisoformat(starts)
        except ValueError:
            raise ValueError(f"Tarih anlaşılmadı: {starts} (örn. 2026-10-28T15:00)") from None

    def _check_hours(self, start: datetime, end: datetime) -> None:
        hours = self.profile["business_hours"]
        open_t = datetime.combine(start.date(), dtime.fromisoformat(hours["open"]))
        close_t = datetime.combine(start.date(), dtime.fromisoformat(hours["close"]))
        if not (open_t <= start and end <= close_t):
            span = f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
            raise ValueError(f"{span} çalışma saatleri dışında ({hours['open']}–{hours['close']})")

    def _check_free(self, staff: str, start: datetime, end: datetime, ignore_id: str | None = None) -> None:
        names = {c.id: c.name for c in self.customers}
        items = {i.id: i for i in self.items}
        for a in self.appointments:
            if a.staff != staff or a.status != "confirmed" or a.id == ignore_id:
                continue
            s, e = datetime.fromisoformat(a.starts), datetime.fromisoformat(a.ends)
            if start < e and s < end:   # same overlap rule get_schedule's gaps are built on
                raise ValueError(f"{staff} o saatte dolu ({s.strftime('%H:%M')}–{e.strftime('%H:%M')} "
                                 f"{items[a.item_id].name}, {names.get(a.customer_id, '?')})")

    def _record_change(self, change: str, a: Appointment, reason: str = "") -> None:
        entry: dict[str, Any] = {"change": change, **asdict(a)}
        if reason:
            entry["reason"] = reason
        self.appointment_changes.append(entry)

    def save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "business": self.business,
            "profile": self.profile,
            "items": [asdict(i) for i in self.items],
            "customers": [asdict(c) for c in self.customers],
            "appointments": [asdict(a) for a in self.appointments],
            "discounts": [asdict(d) for d in self.discounts],
            "campaigns": [asdict(c) for c in self.campaigns],
            "social_posts": [asdict(p) for p in self.social_posts],
            "revenue_entries": [asdict(e) for e in self.revenue_entries],
            "leads": [asdict(l) for l in self.leads],
            "messages": [asdict(m) for m in self.messages],
            "appointment_changes": self.appointment_changes,
        }
        self.store_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    # -- read tools ---------------------------------------------------------- #
    def store_profile(self) -> dict[str, Any]:
        statuses = [c.loyalty_status(self.today) for c in self.customers]
        return {
            **self.profile,
            "today": self.today.isoformat(),
            "weekday": self.today.strftime("%A"),
            "customer_count": len(self.customers),
            "customers_by_loyalty": {s: statuses.count(s) for s in ("active", "at_risk", "lost")},
            "catalog_count": {t: sum(1 for i in self.items if i.type == t and i.active) for t in ITEM_TYPES},
        }

    def list_catalog(self, item_type: str | None = None) -> list[dict[str, Any]]:
        return [asdict(i) for i in self.items if i.active and (item_type in (None, "", i.type))]

    def find_customers(self, inactive_days: int | None = None, min_visits: int = 1,
                       tags: list[str] | None = None) -> dict[str, Any]:
        """Return a segment: customers matching the criteria AND holding marketing consent
        (KEDD's consent gate). Registers the segment so a later campaign can target it by id.
        No phone numbers are returned."""
        hits, no_consent = [], 0
        for c in self.customers:
            days = (self.today - date.fromisoformat(c.last_visit)).days
            if inactive_days is not None and days < inactive_days:
                continue
            if c.visit_count < min_visits:
                continue
            if tags and not set(tags) & set(c.tags):
                continue
            if not c.marketing_consent:
                no_consent += 1
                continue
            hits.append(c)
        seg_id = self._next_id("seg")
        self.segments[seg_id] = [c.id for c in hits]
        return {
            "segment_id": seg_id,
            "criteria": {"inactive_days": inactive_days, "min_visits": min_visits, "tags": tags},
            "count": len(hits),
            "excluded_no_consent": no_consent,
            "sample": [{"name": c.name, "last_visit": c.last_visit, "visit_count": c.visit_count,
                        "loyalty": c.loyalty_status(self.today)} for c in hits[:5]],
        }

    def find_customer(self, name: str) -> list[dict[str, Any]]:
        """Resolve a spoken name to customer ids (accent-insensitive, partial). No phone numbers."""
        q = _fold_name(name or "")
        if not q:
            return []
        return [{"id": c.id, "name": c.name, "last_visit": c.last_visit, "visit_count": c.visit_count}
                for c in self.customers if q in _fold_name(c.name)][:10]

    def find_loyalty_member(self, name: str) -> dict[str, Any]:
        """Resolve a spoken name to loyalty members with current points. member_id is the
        customer id (same identity the gateway uses for members)."""
        q = _fold_name(name or "")
        if not q:
            return {"members": []}
        return {"members": [{"member_id": c.id, "name": c.name, "points": c.loyalty_points,
                             "tier": None}
                            for c in self.customers if q in _fold_name(c.name)][:10]}

    def get_schedule(self, day: str) -> dict[str, Any]:
        """Appointments and free slots for one day (per staff member)."""
        d = date.fromisoformat(day)
        hours = self.profile["business_hours"]
        open_t = datetime.combine(d, dtime.fromisoformat(hours["open"]))
        close_t = datetime.combine(d, dtime.fromisoformat(hours["close"]))
        names = {c.id: c.name for c in self.customers}
        items = {i.id: i for i in self.items}
        todays = sorted((a for a in self.appointments if a.starts.startswith(day) and a.status == "confirmed"),
                        key=lambda a: a.starts)
        free: dict[str, list[dict[str, str]]] = {}
        for s in self.profile["staff"] or ["-"]:
            cursor = open_t
            gaps = []
            for a in (x for x in todays if x.staff == s):
                st, en = datetime.fromisoformat(a.starts), datetime.fromisoformat(a.ends)
                if st - cursor >= timedelta(minutes=30):
                    gaps.append({"from": cursor.strftime("%H:%M"), "to": st.strftime("%H:%M")})
                cursor = max(cursor, en)
            if close_t - cursor >= timedelta(minutes=30):
                gaps.append({"from": cursor.strftime("%H:%M"), "to": close_t.strftime("%H:%M")})
            free[s] = gaps
        return {
            "day": day,
            "business_hours": hours,
            "appointments": [{"time": a.starts[11:16] + "–" + a.ends[11:16], "service": items[a.item_id].name,
                              "staff": a.staff, "customer": names.get(a.customer_id, "?")} for a in todays],
            "free_slots": free,
        }

    def daily_summary(self, day: str) -> dict[str, Any]:
        """Everything a "Bugün ne oldu?" answer needs, derived from seeded data plus today's changes."""
        d = date.fromisoformat(day)   # raises ValueError on a bad date, like get_schedule
        day_appts = [a for a in self.appointments if a.starts[:10] == day and a.status == "confirmed"]
        cancelled = sum(1 for a in self.appointments if a.starts[:10] == day and a.status == "cancelled")
        if d < self.today:
            # No clock in the backend: past days are done; a stable id-based slice counts as no-shows.
            no_shows = [a for a in day_appts if int("".join(ch for ch in a.id if ch.isdigit()) or 0) % 7 == 0]
            completed, upcoming = len(day_appts) - len(no_shows), 0
        else:
            no_shows, completed, upcoming = [], 0, len(day_appts)
        entries = [e for e in self.revenue_entries if e.day == day]
        by_cat: dict[str, float] = {}
        for e in entries:
            by_cat[e.category] = round(by_cat.get(e.category, 0.0) + e.amount, 2)
        inbox = [m for m in self.messages if m.day == day]
        sent = [c for c in self.campaigns if c.day == day]
        added = [i for i in self.items if i.added == day]
        return {
            "day": day,
            "business": self.profile["business_name"],
            "appointments": {"count": len(day_appts), "completed": completed, "upcoming": upcoming,
                             "no_shows": len(no_shows), "cancelled": cancelled},
            "revenue": {"total": round(sum(e.amount for e in entries), 2), "by_category": by_cat,
                        "currency": "TRY"},
            "new_customers": sum(1 for c in self.customers if c.created == day),
            "new_leads": sum(1 for l in self.leads if l.day == day),
            "unanswered_messages": {
                "count": len(inbox),
                "messages": [{"customer": m.customer, "preview": m.preview, "age_minutes": m.age_minutes}
                             for m in inbox[:3]],
            },
            "campaigns_sent": {"count": len(sent), "recipients": sum(c.recipients for c in sent)},
            "catalog_changes": {"count": len(added), "items": [i.name for i in added]},
        }

    def find_appointments(self, customer_name: str | None = None,
                          day: str | None = None) -> list[dict[str, Any]]:
        """Accent-insensitive partial name search; several customers may match. No phone numbers."""
        q = _fold_name(customer_name or "")
        names = {c.id: c.name for c in self.customers}
        items = {i.id: i for i in self.items}
        out = []
        for a in sorted(self.appointments, key=lambda x: x.starts):
            if day and not a.starts.startswith(day):
                continue
            if q and q not in _fold_name(names.get(a.customer_id, "")):
                continue
            svc = items.get(a.item_id)
            out.append({"id": a.id, "customer": names.get(a.customer_id, "?"),
                        "service": svc.name if svc else "?", "staff": a.staff,
                        "starts": a.starts, "ends": a.ends, "status": a.status})
        return out

    def preview_bulk_price(self, item_type: str | None = None, category: str | None = None,
                           item_ids: list[str] | None = None, mode: str | None = None,
                           value: float | None = None) -> dict[str, Any]:
        """Show what a bulk price change would do, without changing anything.

        mode `percent` (e.g. 10) or `amount` in TRY (e.g. 50); value may be negative.
        Registers the preview so `apply_bulk_price` can apply it by id (15 min validity)."""
        if mode not in ("percent", "amount"):
            raise ValueError("mode 'percent' ya da 'amount' olmalı")
        try:
            delta = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"değer sayı olmalı: {value!r}") from None
        if item_type not in (None, "", *ITEM_TYPES):
            raise ValueError(f"item_type {item_type!r} anlaşılmadı (product | service)")
        if item_ids is not None:
            wanted = {i.id: i for i in (self._item(i) for i in item_ids)}   # raises on unknown ids
            items = [i for i in self.items if i.active and i.id in wanted]
        else:
            items = [i for i in self.items if i.active and (not item_type or i.type == item_type)
                     and (not category or i.category == category)]
        rows = [{"id": i.id, "name": i.name, "old_price": i.price,
                 "new_price": round(i.price * (1 + delta / 100), 2) if mode == "percent"
                 else round(i.price + delta, 2)} for i in items]
        pid = self._next_id("pv")
        self.previews[pid] = {"items": rows, "at": time.time()}
        return {"preview_id": pid, "items": rows, "count": len(rows)}

    def ads_performance(self, days: int = 7) -> dict[str, Any]:
        """Ad campaigns with spend / impressions / clicks / results / cost per result + totals."""
        if days is None:
            days = 7
        try:
            days = int(days)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"gün sayısı anlaşılmadı: {days!r}") from None
        if not 1 <= days <= 365:
            raise ValueError("days 1..365 olmalı")
        factor = days / 7
        campaigns = []
        for c in self.ads:
            spend = round(c.spend * factor, 2)
            impr, clicks, results = (int(round(v * factor)) for v in (c.impressions, c.clicks, c.results))
            campaigns.append({"id": c.id, "name": c.name, "platform": c.platform, "status": c.status,
                              "daily_budget": c.daily_budget, "spend": spend, "impressions": impr,
                              "clicks": clicks, "results": results,
                              "cost_per_result": round(spend / results, 2) if results else None})
        totals: dict[str, Any] = {
            "spend": round(sum(x["spend"] for x in campaigns), 2),
            "impressions": sum(x["impressions"] for x in campaigns),
            "clicks": sum(x["clicks"] for x in campaigns),
            "results": sum(x["results"] for x in campaigns),
        }
        totals["cost_per_result"] = (round(totals["spend"] / totals["results"], 2)
                                     if totals["results"] else None)
        return {"campaigns": campaigns, "totals": totals}

    def ads_suggestions(self) -> dict[str, Any]:
        return {"suggestions": [dict(s) for s in self.ad_suggestions]}

    def campaign_templates(self) -> list[dict[str, Any]]:
        """Approved templates {name, body, variables}; {{1}} is always the customer name."""
        return [{"name": t["name"], "body": t["body"],
                 "variables": [dict(v) for v in t["variables"]]} for t in _CAMPAIGN_TEMPLATES]

    def preview_campaign(self, segment_id: str, template: str,
                         variables: dict[str, str] | None = None) -> dict[str, Any]:
        """Dry-run a template campaign: masked template text plus eligible/skipped counts."""
        if segment_id not in self.segments:
            raise ValueError(f"unknown segment {segment_id}")
        bodies = {t["name"]: t["body"] for t in _CAMPAIGN_TEMPLATES}
        if template not in bodies:
            raise ValueError(f"unknown template {template}")
        names = [c.name for cid in self.segments[segment_id]
                 for c in self.customers if c.id == cid]
        first = names[0] if names else "Müşteri"
        rendered = render_campaign_template(
            bodies[template], {"1": first, **{str(k): v for k, v in (variables or {}).items()
                                              if isinstance(v, str)}})
        return {"segment_id": segment_id, "template": template,
                "template_text": _mask_customer_name(rendered, names),
                "recipients": len(self.segments[segment_id]),
                "skipped": {"no_consent": 0, "other": 0}, "dry_run": True}

    # -- write tools (only called by the executor AFTER human approval) ------ #
    def add_catalog_item(self, item_type: str, name: str, price: float, category: str,
                         description: str, image_path: str = "",
                         duration_minutes: int | None = None) -> dict[str, Any]:
        if item_type not in ITEM_TYPES:
            raise ValueError(f"item_type must be one of {ITEM_TYPES}")
        it = CatalogItem(self._next_id("i"), item_type, name, float(price), "TRY", category,
                         description, image_path, duration_minutes=duration_minutes,
                         added=self.today.isoformat())
        self.items.append(it)
        return asdict(it)

    def create_discount(self, name: str, percent: int, item_ids: list[str],
                        starts: str, ends: str) -> dict[str, Any]:
        items = [self._item(i) for i in item_ids]     # raises on unknown ids
        for it in items:
            it.sale_price = round(it.price * (100 - int(percent)) / 100, 2)
            it.sale_starts, it.sale_ends = starts, ends
        d = Discount(self._next_id("d"), name, int(percent), list(item_ids), starts, ends)
        self.discounts.append(d)
        return {**asdict(d), "sale_prices": {it.id: it.sale_price for it in items}}

    def send_campaign(self, name: str, segment_id: str, message: str = "",
                      visual_path: str = "", template: str | None = None,
                      variables: dict[str, str] | None = None) -> dict[str, Any]:
        if segment_id not in self.segments:
            raise ValueError(f"unknown segment {segment_id}")
        n = len(self.segments[segment_id])
        if n > settings.max_campaign_recipients:
            raise ValueError(f"segment too large ({n} > {settings.max_campaign_recipients})")
        variables = dict(variables or {})
        if template:
            bodies = {t["name"]: t["body"] for t in _CAMPAIGN_TEMPLATES}
            if template not in bodies:
                raise ValueError(f"unknown template {template}")
            names = {c.id: c.name for c in self.customers}
            rendered = [render_campaign_template(bodies[template], {"1": names.get(cid, "Müşteri"),
                                                                   **variables})
                        for cid in self.segments[segment_id]]
            message = rendered[0] if rendered else render_campaign_template(
                bodies[template], {"1": "Müşteri", **variables})
        c = Campaign(self._next_id("cmp"), name, segment_id, message, visual_path, n,
                     template=template or "", variables=variables, status="sent",
                     day=self.today.isoformat())
        # Simulated engagement so the report has numbers to show. Labelled "simulated".
        c.replies = int(n * self._rng.uniform(0.18, 0.32))
        c.conversions = int(c.replies * self._rng.uniform(0.25, 0.45))
        self.campaigns.append(c)
        return {**asdict(c), "note": "mock backend: messages are NOT sent; engagement is simulated"}

    def publish_social_post(self, caption: str, image_path: str, platforms: list[str],
                            publish_at: str | None = None,
                            dry_run: bool = False) -> dict[str, Any]:
        if dry_run:
            return {"campaign_id": "", "status": "preview", "scheduled_for": publish_at,
                    "note": "mock backend: dry run, nothing is stored"}
        p = SocialPost(self._next_id("sp"), caption, list(platforms), image_path,
                       publish_at=publish_at, status="scheduled" if publish_at else "published")
        self.social_posts.append(p)
        return {"campaign_id": p.id, "status": p.status, "scheduled_for": p.publish_at,
                "note": "mock backend: social post is simulated, nothing is published"}

    def create_appointment(self, customer_id: str, item_id: str, staff: str,
                           starts: str) -> dict[str, Any]:
        """Book a service. Raises ValueError with a Turkish reason when the slot does not work."""
        customer = self._customer(customer_id)
        try:
            service = self._item(item_id)
        except ValueError:
            raise ValueError(f"Hizmet bulunamadı: {item_id}") from None
        if service.type != "service":
            raise ValueError(f"{service.name} bir hizmet değil, randevu açılamaz")
        if staff not in self.profile["staff"]:
            raise ValueError(f"Çalışan bulunamadı: {staff or '—'}")
        start = self._parse_starts(starts)
        end = start + timedelta(minutes=service.duration_minutes or 30)
        self._check_hours(start, end)
        self._check_free(staff, start, end)
        a = Appointment(self._next_id("a"), customer.id, service.id, staff,
                        start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes"))
        self.appointments.append(a)
        self._record_change("created", a)
        return asdict(a)

    def reschedule_appointment(self, appointment_id: str, starts: str,
                               staff: str | None = None) -> dict[str, Any]:
        """Move an appointment; service and customer stay the same."""
        a = self._appointment(appointment_id)
        if a.status == "cancelled":
            raise ValueError("İptal edilmiş bir randevu taşınamaz")
        service = self._item(a.item_id)
        who = staff or a.staff
        if who not in self.profile["staff"]:
            raise ValueError(f"Çalışan bulunamadı: {who}")
        start = self._parse_starts(starts)
        end = start + timedelta(minutes=service.duration_minutes or 30)
        self._check_hours(start, end)
        self._check_free(who, start, end, ignore_id=a.id)
        a.starts, a.ends, a.staff = (start.isoformat(timespec="minutes"),
                                     end.isoformat(timespec="minutes"), who)
        self._record_change("rescheduled", a)
        return asdict(a)

    def cancel_appointment(self, appointment_id: str, reason: str = "") -> dict[str, Any]:
        a = self._appointment(appointment_id)
        if a.status != "cancelled":
            a.status = "cancelled"
            self._record_change("cancelled", a, reason)
        return asdict(a)

    def apply_bulk_price(self, preview_id: str) -> dict[str, Any]:
        """Apply a previewed bulk price change. Unknown or expired previews fail like the gateway."""
        pv = self.previews.get(preview_id)
        if pv is None:
            raise ValueError(f"Fiyat önizlemesi bulunamadı: {preview_id}")
        if time.time() - pv["at"] > self.PREVIEW_TTL_S:
            raise ValueError("Fiyat önizlemesinin süresi doldu (15 dk), yeniden önizleme iste")
        rows = []
        for row in pv["items"]:
            item = self._item(row["id"])
            old = item.price
            item.price = float(row["new_price"])
            rows.append({"id": item.id, "old_price": old, "new_price": item.price})
        return {"preview_id": preview_id, "items": rows, "count": len(rows)}

    def adjust_loyalty(self, customer_id: str, points: int, reason: str) -> dict[str, Any]:
        """Add (or, when negative, subtract) loyalty points. A balance below zero clamps to 0."""
        customer = self._customer(customer_id)
        try:
            delta = int(points)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"puan sayı olmalı: {points!r}") from None
        old = customer.loyalty_points
        customer.loyalty_points = max(0, old + delta)
        return {"customer_id": customer.id, "name": customer.name, "old_points": old,
                "points": customer.loyalty_points, "delta": delta, "reason": reason}

    def ads_action(self, campaign_id: str, action: str,
                   delta_pct: int | None = None) -> dict[str, Any]:
        """Pause / resume a campaign or change its daily budget by delta_pct (1..20)."""
        campaign = self._ad(campaign_id)
        if action == "pause":
            campaign.status = "paused"
        elif action == "resume":
            campaign.status = "active"
        elif action in ("increase_budget", "decrease_budget"):
            if campaign.status != "active":
                raise ValueError(f"Duraklatılmış kampanyanın bütçesi değiştirilemez: {campaign.name}")
            if delta_pct is None:
                raise ValueError("bütçe değişimi için delta_pct gerekli (1..20)")
            sign = 1 if action == "increase_budget" else -1
            campaign.daily_budget = round(campaign.daily_budget * (1 + sign * int(delta_pct) / 100), 2)
        else:
            raise ValueError(f"reklam işlemi anlaşılmadı: {action!r} "
                             "(pause | resume | increase_budget | decrease_budget)")
        return asdict(campaign)

    def report(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "business": self.profile["business_name"],
            "items_added": [asdict(i) for i in self.items[self._items_at_start:]],
            "active_discounts": [asdict(d) for d in self.discounts],
            "campaigns": [asdict(c) for c in self.campaigns],
            "social_posts": [asdict(p) for p in self.social_posts],
            "appointments_changed": self.appointment_changes,
            "engagement_note": "simulated (mock backend)",
        }


# --------------------------------------------------------------------------- #
# KEDD adapter (private platform; HTTP only, no KEDD source code here)
# --------------------------------------------------------------------------- #

class KeddError(ValueError):
    """The KEDD gateway failed (unreachable, 5xx, 429, 403 …), not the request itself.

    A ValueError subclass so every existing handler keeps working; channels catch this
    specifically to say "KEDD'ye ulaşılamıyor" instead of a generic failure.
    """


SOCIAL_JPG_MAX_BYTES = 5 * 1024 * 1024


def _jpg_under_limit(raw: bytes, limit: int = SOCIAL_JPG_MAX_BYTES) -> bytes:
    """Re-encode any image as JPEG under `limit`, shrinking only when needed."""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:  # noqa: BLE001 — any undecodable payload is the same user error
        raise ValueError("Gönderi görseli okunamadı, planı tekrar çalıştırır mısın?") from None
    for quality, scale in ((85, 1.0), (75, 1.0), (75, 0.75), (65, 0.5), (60, 0.35), (55, 0.25)):
        frame = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                           Image.LANCZOS) if scale != 1.0 else img
        buf = io.BytesIO()
        frame.save(buf, format="JPEG", quality=quality)
        if len(buf.getvalue()) <= limit:
            return buf.getvalue()
    raise ValueError("Görsel 5 MB altına sığmadı, başka bir fotoğraf dener misin?")

# A 409 means the gateway has seen this idempotency key before: the operation may already
# have run, so it must never be retried automatically. Shown to the owner via the executor.
_IDEMPOTENCY_CONFLICT_TR = {
    "in_progress": "Bu işlem şu anda çalışıyor olabilir; çift çalışmaması için tekrar denemedim",
    "unknown_outcome": ("Bu işlemin sonucu belirsiz (daha önce çalışmış olabilir); "
                        "güvenlik için tekrar denemedim"),
}

# Capabilities in GET /agent/report that count as agent writes for the Live-in-KEDD panel.
# Reads (catalog.list, segments.create, …) never appear there; appointments.* all merge.
_WRITE_CAPABILITIES = frozenset({
    "catalog.create", "catalog.update", "catalog.bulkPriceApply",
    "discounts.create", "campaigns.send",
})

#: Placeholder shown wherever a customer name would otherwise leak to the model or the card.
MASKED_NAME = "‹müşteri adı›"


def _mask_customer_name(text: str, names: list[str]) -> str:
    """Mask a filled-in customer name in a gateway-rendered template text.

    The gateway fills {{1}} with the first eligible customer's real name, so the
    preview replaces it — full name and first token both, since either form may
    arrive — plus any leftover {{1}} for gateways that leave it unfilled.
    Over-masking errs private; a masked preview never leaks a name.
    """
    out = text
    for n in names:
        if n and len(n) > 1:
            out = out.replace(n, MASKED_NAME)
            first = n.split()[0]
            if len(first) > 1 and first != n:
                out = out.replace(first, MASKED_NAME)
    return out.replace("{{1}}", MASKED_NAME)


def _as_int(v: Any) -> int | None:
    """JSON number → int (bools and non-integral floats are garbage, not numbers)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


class KeddBackend:
    """Adapter onto KEDD's agent gateway (a handful of signed HTTPS endpoints).

    Security contract (enforced on the KEDD side, mirrored here):
      * every request carries the merchant's WhatsApp phone, a timestamp and an
        HMAC-SHA256 signature over "<timestamp>.<method>.<path?query>.<body>"; the gateway
        resolves the business from its own phone→business mapping, never from the client
      * writes carry an Idempotency-Key so a retry can never send a campaign twice
      * customer phone numbers never leave KEDD: segments come back as counts + samples
    The platform itself is proprietary and not included; with BACKEND=mock nothing here runs.

    Shape contract (task 11): every method returns exactly what MockBackend returns for the
    same method — same keys, same wrapping — mapped from the gateway's real responses.
    Fields the gateway cannot provide come back as None (scalars) or [] (lists); request
    arguments the gateway cannot express raise ValueError with a Turkish reason instead of
    silently changing meaning. No phone number ever passes through.
    """

    name = "kedd"
    # Same staleness contract as MockBackend: a session pickled before a shape change
    # is re-seeded instead of crashing halfway through an approved plan.
    SCHEMA = 4  # 4: segment sample names cached for preview masking

    def __init__(self, base_url: str, secret: str, merchant_phone: str, key_id: str = "main"):
        import requests  # local import keeps mock mode dependency-light
        self._s = requests.Session()
        self.base = base_url.rstrip("/")
        self._secret = secret.encode()
        self._phone = merchant_phone
        self._key_id = key_id  # "main" or "demo": selects the secret server-side (contract §1)
        self._seg_names: dict[str, list[str]] = {}  # segment_id -> sample names (preview masking)
        self._schema = self.SCHEMA

    def __repr__(self) -> str:
        return f"KeddBackend(base={self.base!r}, phone=…{self._phone[-4:]})"

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state.pop("_s", None)       # a requests.Session cannot be pickled; recreated on load
        state.pop("_secret", None)
        state.pop("_recent", None)  # never persist the HMAC secret with a session; re-read from config on load
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        import requests  # local import keeps mock mode dependency-light
        self.__dict__.update(state)
        self._s = requests.Session()
        # A demo session restored with the MAIN secret would silently escalate: re-read
        # whichever secret matches the key id the session was pickled with.
        key_id = state.get("_key_id", "main")
        self._key_id = key_id
        secret = settings.kedd_demo_secret if key_id == "demo" else settings.kedd_agent_secret
        self._secret = secret.encode()
        self._seg_names = state.get("_seg_names", {})

    def _unused_timestamp(self, signed: tuple[str, ...]) -> str:
        # The gateway rejects any signature it has already seen (replay guard), and two identical reads in
        # the same second sign identically. Wait for the next second instead of being refused.
        while True:
            ts = str(int(time.time()))
            recent = {r for r in getattr(self, "_recent", set()) if r[0] == ts}
            if (ts, *signed) not in recent:
                self._recent = recent | {(ts, *signed)}
                return ts
            time.sleep(0.05)

    def _headers(self, method: str, path: str, body: str, idempotency_key: str = "") -> dict[str, str]:
        # Phone and Idempotency-Key are inside the signature: a captured request cannot be replayed
        # for another merchant or under another key (gateway K1.2; the old format is rejected).
        ts = self._unused_timestamp((method, path, idempotency_key, body))
        msg = f"{ts}.{method}.{path}.{self._phone}.{idempotency_key}.{body}".encode()
        sig = hmac.new(self._secret, msg, hashlib.sha256).hexdigest()
        headers = {"X-Agent-Phone": self._phone, "X-Agent-Timestamp": ts, "X-Agent-Signature": sig,
                   "X-Agent-Key-Id": self._key_id, "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _gateway_error(status: int, payload: Any) -> KeddError:
        # Failures arrive as {error, message} JSON. The executor shows the message to the
        # owner, so it must be the Turkish message — never a generic HTTP error dump.
        body = payload if isinstance(payload, dict) else {}
        code, msg = body.get("error", ""), body.get("message") or ""
        if status == 409 and code in _IDEMPOTENCY_CONFLICT_TR:
            text = _IDEMPOTENCY_CONFLICT_TR[code]
            return KeddError(f"{text} ({msg})" if msg else text)
        if msg:
            return KeddError(msg)
        if code:
            return KeddError(f"KEDD hatası: {code} (HTTP {status})")
        return KeddError(f"KEDD bağlantısı hata verdi (HTTP {status})")

    @staticmethod
    def _error_payload(r) -> Any:
        try:
            return r.json()
        except ValueError:
            return {}

    def _get(self, path: str, **params):
        # The query string is part of the signed target, exactly as sent on the wire.
        import requests  # local import keeps mock mode dependency-light
        from urllib.parse import urlencode
        qs = urlencode({k: v for k, v in params.items() if v not in (None, "")})
        target = f"{path}?{qs}" if qs else path
        # Reads fail fast: a hung gateway must surface in seconds so the demo chat falls back instead of
        # hanging (writes below keep the patient 30 s). One retry on timeout covers a cold-starting gateway;
        # reads have no side effects, writes are never retried.
        for attempt in (1, 2):
            try:
                r = self._s.get(f"{self.base}{target}", headers=self._headers("GET", target, ""), timeout=8)
                break
            except requests.exceptions.Timeout:
                if attempt == 2:
                    raise KeddError("KEDD'ye ulaşılamadı.") from None
            except requests.exceptions.RequestException:
                raise KeddError("KEDD'ye ulaşılamadı.") from None
        if r.status_code >= 400:
            raise self._gateway_error(r.status_code, self._error_payload(r))
        return r.json()

    def _post(self, path: str, body: dict, idempotency_key: str | None = None):
        import requests  # local import keeps mock mode dependency-light
        # The gateway reads absent and null identically; dropping Nones keeps bodies exact.
        payload = json.dumps({k: v for k, v in body.items() if v is not None},
                             ensure_ascii=False, separators=(",", ":"))
        headers = self._headers("POST", path, payload, idempotency_key or str(uuid.uuid4()))
        try:
            r = self._s.post(f"{self.base}{path}", data=payload.encode(), headers=headers, timeout=30)
        except requests.exceptions.RequestException:
            raise KeddError("KEDD'ye ulaşılamadı; işlemin durumu belirsiz, tekrar denemedim.") from None
        if r.status_code >= 400:
            raise self._gateway_error(r.status_code, self._error_payload(r))
        return r.json()

    # -- shape helpers (all static; gateway truth enters only through these) -- #

    @staticmethod
    def _f(v: Any) -> float | None:
        """JSON number → float; anything else (null included) → None."""
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    @staticmethod
    def _tr(iso: Any) -> datetime | None:
        """Gateway ISO instant → aware Europe/Istanbul datetime; unparseable → None."""
        if not isinstance(iso, str) or not iso:
            return None
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError:
            return None
        from zoneinfo import ZoneInfo  # local import keeps mock mode dependency-light
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(ZoneInfo("Europe/Istanbul"))

    @classmethod
    def _tr_minutes(cls, iso: Any) -> str | None:
        dt = cls._tr(iso)
        return dt.strftime("%Y-%m-%dT%H:%M") if dt else None

    @classmethod
    def _tr_hhmm(cls, iso: Any) -> str | None:
        dt = cls._tr(iso)
        return dt.strftime("%H:%M") if dt else None

    @staticmethod
    def _map_status(status: Any) -> Any:
        # The mock calls an upcoming booking "confirmed"; the gateway calls it "pending".
        return "confirmed" if status == "pending" else status

    @staticmethod
    def _weekday_name(day: str) -> str:
        try:
            return ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")[
                date.fromisoformat(day).weekday()]
        except ValueError:
            return ""

    @classmethod
    def _day_hours(cls, hours: Any, day: str) -> dict[str, Any]:
        """Weekly opening-hours blob + ISO day → mock-shaped {"open", "close"} (None when unknown)."""
        entries = hours.get("hours") if isinstance(hours, dict) else None
        want = cls._weekday_name(day)
        if isinstance(entries, list) and want:
            for h in entries:
                if isinstance(h, dict) and h.get("dayOfWeek") == want and h.get("isOpen"):
                    return {"open": h.get("openTime"), "close": h.get("closeTime")}
        return {"open": None, "close": None}

    @staticmethod
    def _slots_to_gaps(slots: Any) -> list[dict[str, str]]:
        """Flat [{time, available}] → merged [{"from", "to"}] ranges over consecutive open slots."""
        def mins(t: str) -> int | None:
            try:
                h, m = t.split(":")
                v = int(h) * 60 + int(m)
                return v if 0 <= v < 24 * 60 else None
            except (ValueError, AttributeError):
                return None
        open_mins = sorted({m for s in (slots or []) if isinstance(s, dict) and s.get("available")
                            for m in [mins(s.get("time", ""))] if m is not None})
        if not open_mins:
            return []
        diffs = [b - a for a, b in zip(open_mins, open_mins[1:]) if b - a > 0]
        step = min(diffs) if diffs else 30
        gaps, start, prev = [], open_mins[0], open_mins[0]
        for m in open_mins[1:] + [None]:
            if m is not None and m - prev == step:
                prev = m
                continue
            gaps.append({"from": f"{start // 60:02d}:{start % 60:02d}",
                         "to": f"{min(prev + step, 24 * 60 - 1) // 60:02d}:"
                               f"{min(prev + step, 24 * 60 - 1) % 60:02d}"})
            start = prev = m  # type: ignore[assignment]
        return gaps

    @staticmethod
    def _date_part(s: Any) -> str:
        return str(s).split("T")[0]

    @staticmethod
    def _parse_starts(starts: str) -> datetime:
        """Agent-side ISO start → naive TR datetime (mirrors MockBackend's Turkish error)."""
        try:
            dt = datetime.fromisoformat(starts)
        except (ValueError, TypeError):
            raise ValueError(f"Tarih anlaşılmadı: {starts} (örn. 2026-10-28T15:00)") from None
        if dt.tzinfo is not None:
            from zoneinfo import ZoneInfo  # local import keeps mock mode dependency-light
            dt = dt.astimezone(ZoneInfo("Europe/Istanbul")).replace(tzinfo=None)
        return dt

    def _staff_id(self, staff: str) -> str:
        """Resolve a staff NAME to the gateway's staff id via the profile's staff list."""
        res = self._get("/agent/profile")
        ids = {s.get("name"): s.get("id") for s in res.get("staff") or [] if isinstance(s, dict)}
        if staff not in ids or not ids[staff]:
            raise ValueError(f"Çalışan bulunamadı: {staff or '—'}")
        return ids[staff]

    # -- read tools (mock shapes, gateway truth) -- #

    def store_profile(self) -> dict[str, Any]:
        res = self._get("/agent/profile")
        biz = res.get("business") or {}
        today = res.get("today") or ""
        try:
            items = self._get("/agent/catalog").get("items") or []
            counts = {t: sum(1 for i in items if i.get("type") == t) for t in ("product", "service")}
        except ValueError:
            counts = {"product": None, "service": None}  # e.g. the business has no catalog module
        try:
            weekday = date.fromisoformat(today).strftime("%A")
        except ValueError:
            weekday = ""
        return {
            "business_name": biz.get("name") or "",
            "city": biz.get("city") or "",
            "sector": biz.get("sector") or "",
            "channels": ["WhatsApp"],
            "tone": None,
            "currency": "TRY",
            "business_hours": self._day_hours(biz.get("hours"), today),
            "staff": [s.get("name", "") for s in res.get("staff") or [] if isinstance(s, dict)],
            "today": today,
            "weekday": weekday,
            "customer_count": None,
            "customers_by_loyalty": None,
            "catalog_count": counts,
        }

    def list_catalog(self, item_type: str | None = None) -> list[dict[str, Any]]:
        res = self._get("/agent/catalog", type=item_type or "")
        out = []
        for it in res.get("items") or []:
            out.append({
                "id": it.get("id", ""), "type": it.get("type", ""), "name": it.get("name", ""),
                "price": self._f(it.get("price")), "currency": "TRY",
                "category": it.get("category") or "", "description": "", "image_path": "",
                "duration_minutes": it.get("durationMinutes"), "sale_price": self._f(it.get("salePrice")),
                "sale_starts": None, "sale_ends": None, "active": True, "added": "",
            })
        return out

    def find_customers(self, inactive_days: int | None = None, min_visits: int = 1,
                       tags: list[str] | None = None) -> dict[str, Any]:
        res = self._post("/agent/segments", {"inactive_days": inactive_days,
                                             "min_visits": min_visits, "tags": tags})
        seg_id = res.get("segment_id", "")
        sample = [{"name": e.get("name", ""), "last_visit": e.get("lastVisit"),
                   "visit_count": None, "loyalty": None} for e in res.get("examples") or []]
        # Sample names mask the filled-in {{1}} in a later campaign preview of this segment.
        self._seg_names[seg_id] = [s["name"] for s in sample if s["name"]]
        if len(self._seg_names) > 50:
            self._seg_names.pop(next(iter(self._seg_names)))
        return {
            "segment_id": seg_id,
            "criteria": {"inactive_days": inactive_days, "min_visits": min_visits, "tags": tags},
            "count": res.get("count", 0),
            "excluded_no_consent": None,
            "sample": sample,
        }

    def get_schedule(self, day: str) -> dict[str, Any]:
        res = self._get("/agent/schedule", day=day)
        try:
            hours = (self._get("/agent/profile").get("business") or {}).get("hours")
        except ValueError:
            hours = None
        appts = []
        for a in res.get("appointments") or []:
            if (a.get("status") or "") == "cancelled":
                continue
            sh, eh = self._tr_hhmm(a.get("starts")), self._tr_hhmm(a.get("ends"))
            appts.append({"time": f"{sh or '?'}–{eh or '?'}", "service": a.get("service") or "?",
                          "staff": a.get("staff") or "", "customer": a.get("customer") or ""})
        return {"day": res.get("day", day), "business_hours": self._day_hours(hours, day),
                "appointments": appts, "free_slots": {"-": self._slots_to_gaps(res.get("slots"))}}

    def find_customer(self, name: str) -> list[dict[str, Any]]:
        res = self._get("/agent/customers", name=name)
        return [{"id": m.get("id", ""), "name": m.get("name", ""), "last_visit": m.get("lastVisit"),
                 "visit_count": m.get("visitCount", 0)} for m in res.get("items") or []]

    def daily_summary(self, day: str) -> dict[str, Any]:
        res = self._get("/agent/daily-summary", day=day)
        ap, rev, oc = res.get("appointments") or {}, res.get("revenue") or {}, \
            res.get("outboundCampaigns") or {}
        by_status = ap.get("byStatus") or {}
        return {
            "day": res.get("day", day),
            "business": None,
            "appointments": {"count": ap.get("total", 0), "completed": by_status.get("completed", 0),
                             "upcoming": by_status.get("pending", 0),
                             "no_shows": by_status.get("no_show", 0),
                             "cancelled": by_status.get("cancelled", 0)},
            "revenue": {"total": self._f(rev.get("paidTotal")) or 0.0, "by_category": None,
                        "currency": "TRY"},
            "new_customers": res.get("newCustomers", 0),
            "new_leads": res.get("newLeads", 0),
            "unanswered_messages": {"count": res.get("missedWhatsapp", 0), "messages": []},
            "campaigns_sent": {"count": oc.get("sent", 0), "recipients": None},
            "catalog_changes": {"count": None, "items": []},
        }

    def find_appointments(self, customer_name: str | None = None,
                          day: str | None = None) -> list[dict[str, Any]]:
        res = self._get("/agent/appointments", customer_name=customer_name or "", day=day or "")
        return [{"id": a.get("id", ""), "customer": a.get("customer") or "",
                 "service": a.get("service") or "?", "staff": a.get("staff") or "",
                 "starts": self._tr_minutes(a.get("starts")), "ends": self._tr_minutes(a.get("ends")),
                 "status": self._map_status(a.get("status"))} for a in res.get("appointments") or []]

    def preview_bulk_price(self, item_type: str | None = None, category: str | None = None,
                           item_ids: list[str] | None = None, mode: str | None = None,
                           value: float | None = None) -> dict[str, Any]:
        if mode != "percent":
            raise ValueError("KEDD toplu fiyat yalnız yüzde (percent) ile çalışır")
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"değer sayı olmalı: {value!r}")
        if item_ids:
            raise ValueError("KEDD önizleme kalem seçmez; tür/kategori ile daralt")
        res = self._post("/agent/catalog/bulk-price/preview",
                         {"percent": value, "type": item_type or None, "category": category or None})
        return {"preview_id": res.get("preview_id", ""),
                "items": [{"id": i.get("id", ""), "name": i.get("name", ""),
                           "old_price": self._f(i.get("before")), "new_price": self._f(i.get("after"))}
                          for i in res.get("items") or []],
                "count": res.get("count", 0)}

    def ads_performance(self, days: int = 7) -> dict[str, Any]:
        if days is None:
            days = 7
        try:
            days = int(days)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"gün sayısı anlaşılmadı: {days!r}") from None
        res = self._get("/agent/ads/performance", days=days)
        campaigns = []
        for c in res.get("campaigns") or []:
            spend = self._f(c.get("spend")) or 0.0
            results = c.get("results")
            results = int(results) if isinstance(results, (int, float)) and not isinstance(
                results, bool) else None
            budget = None
            for key in ("dailyBudget", "daily_budget", "budget"):  # wire name still unconfirmed
                budget = self._f(c.get(key))
                if budget is not None:
                    break
            campaigns.append({
                "id": c.get("id", ""), "name": c.get("name", ""), "platform": None,
                "status": c.get("status", ""), "daily_budget": budget, "spend": spend,
                "impressions": int(c.get("impressions") or 0), "clicks": int(c.get("clicks") or 0),
                "results": results,
                "cost_per_result": round(spend / results, 2) if results else None})
        totals: dict[str, Any] = {
            "spend": round(sum(x["spend"] for x in campaigns), 2),
            "impressions": sum(x["impressions"] for x in campaigns),
            "clicks": sum(x["clicks"] for x in campaigns),
            "results": None if any(x["results"] is None for x in campaigns)
            else sum(x["results"] for x in campaigns),
        }
        totals["cost_per_result"] = (round(totals["spend"] / totals["results"], 2)
                                    if totals["results"] else None)
        return {"campaigns": campaigns, "totals": totals}

    def ads_suggestions(self) -> dict[str, Any]:
        res = self._get("/agent/ads/suggestions")
        return {"suggestions": [{"campaign_id": s.get("campaignId"), "kind": None,
                                 "text": s.get("title", "")} for s in res.get("suggestions") or []]}

    def campaign_templates(self) -> list[dict[str, Any]]:
        res = self._get("/agent/campaign-templates")
        return res if isinstance(res, list) else res.get("templates", [])

    # -- write tools (only called by the executor AFTER human approval) -- #

    def add_catalog_item(self, item_type: str, name: str, price: float, category: str,
                         description: str, image_path: str = "",
                         duration_minutes: int | None = None,
                         idempotency_key: str | None = None) -> dict[str, Any]:
        # image_path has no gateway field; the item is created without the photo.
        res = self._post("/agent/catalog", {"type": item_type, "name": name, "price": price,
                                            "category": category, "description": description,
                                            "duration_minutes": duration_minutes}, idempotency_key)
        return {"id": res.get("id", ""), "type": res.get("type", item_type),
                "name": res.get("name", name), "price": self._f(res.get("price", price)) or 0.0,
                "currency": "TRY", "category": category, "description": description, "image_path": "",
                "duration_minutes": duration_minutes, "sale_price": None, "sale_starts": None,
                "sale_ends": None, "active": True, "added": date.today().isoformat()}

    def create_discount(self, name: str, percent: int, item_ids: list[str],
                        starts: str, ends: str,
                        idempotency_key: str | None = None) -> dict[str, Any]:
        # The gateway prices one item per call; a retried approval reuses the same keys.
        item_ids = list(item_ids or [])
        start_day, end_day = self._date_part(starts), self._date_part(ends)
        base_key = idempotency_key or uuid.uuid4().hex
        sale_prices = {}
        for i, item_id in enumerate(item_ids):
            key = base_key if len(item_ids) == 1 else f"{base_key}#{i}"
            res = self._post("/agent/discounts", {"item_id": item_id, "percent": percent,
                                                  "start_day": start_day, "end_day": end_day}, key)
            sale_prices[item_id] = self._f(res.get("sale_price"))
        digest = hashlib.sha1(f"{start_day}|{end_day}|{','.join(sorted(item_ids))}".encode()
                              ).hexdigest()[:8]
        return {"id": f"dsc-{start_day}-{digest}", "name": name, "percent": percent,
                "item_ids": item_ids, "starts": start_day, "ends": end_day, "sale_prices": sale_prices}

    def _template_body_and_vars(self, template: str,
                                    variables: dict[str, str] | None) -> tuple[str, dict[str, str]]:
        """Translate plan variables ({"2": ...}) to gateway names ({"code": ...}).

        Number→name comes from the template metadata itself, so a renamed variable can
        never silently fill the wrong slot. Unknown templates or slots fail closed.
        """
        metas = {t.get("name"): t for t in self.campaign_templates()
                 if isinstance(t, dict)}
        tmpl = metas.get(template)
        if tmpl is None:
            raise ValueError(f"şablon bulunamadı: {template}")
        num2name = {str(v.get("number")): v.get("name") for v in tmpl.get("variables") or []}
        named = {}
        for k, v in (variables or {}).items():
            name = num2name.get(str(k))
            if not name or not isinstance(v, str):
                raise ValueError(f"şablon değişkeni anlaşılmadı: {template}[{k}]")
            named[name] = v
        return str(tmpl.get("body", "")), named

    @staticmethod
    def _skipped_counts(skipped: Any) -> dict[str, int]:
        raw = skipped if isinstance(skipped, dict) else {}
        no_consent = _as_int(raw.get("no_consent")) or 0
        other = sum(n for n in (_as_int(v) for k, v in raw.items() if k != "no_consent") if n)
        return {"no_consent": no_consent, "other": other}

    def preview_campaign(self, segment_id: str, template: str,
                         variables: dict[str, str] | None = None) -> dict[str, Any]:
        """Dry-run a template campaign: masked template text plus eligible/skipped counts.

        Read-only (dry_run is hardcoded true — there is no parameter to flip it), so the
        demo key may call it: the preview IS the demo's campaign experience.
        """
        body, named = self._template_body_and_vars(template, variables)
        res = self._post("/agent/campaigns", {"segment_id": segment_id, "template": template,
                                              "variables": named, "dry_run": True})
        text = _mask_customer_name(str(res.get("template_text") or ""),
                                   self._seg_names.get(segment_id, []))
        return {"segment_id": segment_id, "template": template, "template_text": text,
                "recipients": _as_int(res.get("recipients")) or 0,
                "skipped": self._skipped_counts(res.get("skipped")), "dry_run": True}

    def send_campaign(self, name: str, segment_id: str, message: str = "",
                      visual_path: str = "", template: str | None = None,
                      variables: dict[str, str] | None = None,
                      idempotency_key: str | None = None) -> dict[str, Any]:
        if self._key_id == "demo":
            # Preview-only demo: refuse before touching the network (the gateway's demo
            # policy is the second lock). The approval card already showed the preview.
            raise ValueError("Demo işletmede kampanya gönderimi kapalı; karttaki önizlemeyle yetin.")
        if not template:
            raise ValueError("KEDD kampanya gönderimi şablon ister (şablonsuz mesaj gönderilemez)")
        body, named = self._template_body_and_vars(template, variables)
        res = self._post("/agent/campaigns", {"segment_id": segment_id, "template": template,
                                              "variables": named, "dry_run": False},
                         idempotency_key)
        sent = _as_int(res.get("sent")) or 0
        failed = _as_int(res.get("failed")) or 0
        skipped = self._skipped_counts(res.get("skipped"))
        note = f"{sent} gönderildi"
        if skipped["no_consent"] + skipped["other"]:
            note += f" · {skipped['no_consent'] + skipped['other']} atlandı"
        if failed:
            note += f" · {failed} başarısız"
        return {"id": "", "name": name, "segment_id": segment_id,
                "message": render_campaign_template(
                    body, {"1": MASKED_NAME, **{str(k): v for k, v in (variables or {}).items()
                                               if isinstance(v, str)}}),
                "visual_path": "", "recipients": _as_int(res.get("recipients")) or 0,
                "template": template, "variables": dict(variables or {}), "status": "sent",
                "replies": None, "conversions": None, "day": date.today().isoformat(),
                "note": note}

    def create_appointment(self, customer_id: str, item_id: str, staff: str,
                           starts: str, idempotency_key: str | None = None) -> dict[str, Any]:
        start = self._parse_starts(starts)
        staff_id = self._staff_id(staff)
        res = self._post("/agent/appointments", {"customer_id": customer_id, "catalog_item_id": item_id,
                                                 "date": start.strftime("%Y-%m-%d"),
                                                 "time": start.strftime("%H:%M"),
                                                 "staff_id": staff_id}, idempotency_key)
        dur = res.get("duration_minutes")
        end = start + timedelta(minutes=dur if isinstance(dur, (int, float)) else 0)
        return {"id": res.get("id", ""), "customer_id": customer_id, "item_id": item_id,
                "staff": res.get("staff") or staff,
                "starts": start.isoformat(timespec="minutes"), "ends": end.isoformat(timespec="minutes"),
                "status": self._map_status(res.get("status"))}

    def reschedule_appointment(self, appointment_id: str, starts: str,
                               staff: str | None = None,
                               idempotency_key: str | None = None) -> dict[str, Any]:
        if staff is not None:
            raise ValueError("KEDD randevu taşıma personeli değiştiremez")
        start = self._parse_starts(starts)
        res = self._post("/agent/appointments/reschedule", {"appointment_id": appointment_id,
                                                             "date": start.strftime("%Y-%m-%d"),
                                                             "time": start.strftime("%H:%M")},
                         idempotency_key)
        dur = res.get("duration_minutes")
        end = start + timedelta(minutes=dur if isinstance(dur, (int, float)) else 0)
        return {"id": res.get("id", appointment_id), "customer_id": None, "item_id": None,
                "staff": None, "starts": start.isoformat(timespec="minutes"),
                "ends": end.isoformat(timespec="minutes"), "status": "confirmed"}

    def cancel_appointment(self, appointment_id: str, reason: str = "",
                           idempotency_key: str | None = None) -> dict[str, Any]:
        # reason has no gateway field; the cancellation itself is what matters.
        res = self._post("/agent/appointments/cancel", {"appointment_id": appointment_id},
                         idempotency_key)
        return {"id": res.get("id", appointment_id), "customer_id": None, "item_id": None,
                "staff": None, "starts": None, "ends": None,
                "status": self._map_status(res.get("status", "cancelled"))}

    def apply_bulk_price(self, preview_id: str,
                         idempotency_key: str | None = None) -> dict[str, Any]:
        res = self._post("/agent/catalog/bulk-price", {"preview_id": preview_id}, idempotency_key)
        return {"preview_id": res.get("preview_id", preview_id), "items": [],
                "count": res.get("applied", 0)}

    def find_loyalty_member(self, name: str) -> dict[str, Any]:
        """Loyalty members matching a (partial) name. Built against the brief's fixture
        shape — GET /agent/loyalty/members is not deployed yet (gateway K5.1)."""
        res = self._get("/agent/loyalty/members", name=name)
        members = []
        for m in res.get("members") or []:
            if not isinstance(m, dict):
                continue
            members.append({"member_id": m.get("member_id", ""),
                            "name": m.get("name", ""),
                            "points": _as_int(m.get("points")) or 0,
                            "tier": m.get("tier") if isinstance(m.get("tier"), str) else None})
        return {"members": members}

    def adjust_loyalty(self, customer_id: str, points: int, reason: str,
                       idempotency_key: str | None = None) -> dict[str, Any]:
        # customer_id carries the member_id the planner resolved via find_loyalty_member
        # (same identity). Loyalty is a reversible write, so the demo key may call it.
        delta = _as_int(points)
        if delta is None:
            raise ValueError(f"puan sayı olmalı: {points!r}")
        if delta == 0:
            raise ValueError("puan 0 olamaz (eklemek için pozitif, düşmek için negatif yaz)")
        if abs(delta) > 1000:
            raise ValueError(f"puan tek seferde en fazla 1000 olabilir: {delta}")
        res = self._post("/agent/loyalty/adjust", {"member_id": customer_id, "delta": delta,
                                                  "reason": reason},
                         idempotency_key)
        return {"customer_id": res.get("member_id", customer_id), "name": None,
                "old_points": _as_int(res.get("points_before")) or 0,
                "points": _as_int(res.get("points_after")) or 0, "delta": delta, "reason": reason}

    def ads_action(self, campaign_id: str, action: str,
                   delta_pct: int | None = None,
                   idempotency_key: str | None = None,
                   reason: str | None = None) -> dict[str, Any]:
        if self._key_id == "demo":
            # Ads stay refused on the demo key, before any network call.
            raise ValueError("Demo işletmede reklam işlemi kapalı.")
        if action not in ("pause", "resume", "increase_budget", "decrease_budget"):
            raise ValueError(f"reklam işlemi anlaşılmadı: {action!r} "
                             "(pause | resume | increase_budget | decrease_budget)")
        if action in ("increase_budget", "decrease_budget"):
            if _as_int(delta_pct) is None:
                raise ValueError("bütçe değişimi için delta_pct gerekli (1..20)")
            if abs(_as_int(delta_pct) or 0) > 20:  # checked before any network call
                raise ValueError(f"bütçe tek seferde en fazla %20 değişebilir: {delta_pct}")
        perf = {c["id"]: c for c in self.ads_performance(7).get("campaigns") or []}
        camp = perf.get(campaign_id)
        if camp is None:
            raise ValueError(f"Reklam bulunamadı: {campaign_id}")
        why = (reason or "Çırak planı (sahibi onayladı)").strip()[:200]
        if action in ("pause", "resume"):
            res = self._post("/agent/ads/action", {"campaign_id": campaign_id, "action": action,
                                                  "reason": why}, idempotency_key)
            status = {"pause": "paused", "resume": "active"}[action]
            after = self._f(res.get("budget_after"))
            budget = camp.get("daily_budget") if after is None else after
        else:
            if camp.get("status") != "active":
                raise ValueError(f"Duraklatılmış kampanyanın bütçesi değiştirilemez: {camp['name']}")
            old = camp.get("daily_budget")
            if not isinstance(old, (int, float)) or isinstance(old, bool):
                raise ValueError(f"kampanyanın bütçe bilgisi alınamadı: {camp['name']}")
            new = round(old * (1 + (_as_int(delta_pct) or 0) / 100
                                 * (1 if action == "increase_budget" else -1)), 2)
            res = self._post("/agent/ads/action", {"campaign_id": campaign_id,
                                                  "action": "set_budget", "budget": new,
                                                  "reason": why}, idempotency_key)
            after = self._f(res.get("budget_after"))
            status, budget = camp.get("status", ""), new if after is None else after
        return {"id": res.get("campaign_id", campaign_id), "name": camp["name"], "platform": None,
                "status": status, "daily_budget": budget,
                "spend": camp.get("spend"), "impressions": camp.get("impressions"),
                "clicks": camp.get("clicks"), "results": camp.get("results")}

    def publish_social_post(self, caption: str, image_path: str, platforms: list[str],
                            publish_at: str | None = None,
                            idempotency_key: str | None = None,
                            dry_run: bool = False) -> dict[str, Any]:
        if self._key_id == "demo":
            # Preview-only demo, even for dry runs: a "preview" outcome would render as
            # "paylaşıldı", so refuse honestly instead of faking a share.
            raise ValueError("Demo işletmede gönderi paylaşımı kapalı;"
                             " karttaki görseli önizleme say.")
        if dry_run:
            return {"campaign_id": "", "status": "preview", "scheduled_for": publish_at, "note": ""}
        try:
            image_bytes = Path(image_path).read_bytes()
        except OSError:
            raise ValueError("Gönderi görseli okunamadı, planı tekrar çalıştırır mısın?") from None
        payload = base64.b64encode(_jpg_under_limit(image_bytes)).decode()
        res = self._post("/agent/social/post",
                         {"caption": caption, "platforms": list(platforms), "publish_at": publish_at,
                          "image_base64": payload},
                         idempotency_key)
        return {"campaign_id": res.get("id", ""),
                "status": res.get("status") or ("scheduled" if publish_at else "published"),
                "scheduled_for": res.get("scheduled_for", publish_at), "note": ""}

    def report(self) -> dict[str, Any]:
        self._get("/agent/report")  # usage counters have no mock counterpart; the call only checks liveness
        try:
            business = (self._get("/agent/profile").get("business") or {}).get("name")
        except ValueError:
            business = None
        return {"backend": self.name, "business": business, "items_added": [],
                "active_discounts": [], "campaigns": [], "social_posts": [],
                "appointments_changed": [], "engagement_note": None}

    def agent_write_counts(self) -> dict[str, Any]:
        """Last-window agent writes per action, for the Live-in-KEDD panel.

        The gateway counts every capability (reads included); the panel shows writes
        only, with all appointments.* merged into one row. Unknown capabilities and
        malformed counts are skipped, never crash the panel.
        """
        res = self._get("/agent/report")
        body = res if isinstance(res, dict) else {}
        window = body.get("window_hours")
        window = window if isinstance(window, int) and not isinstance(window, bool) else None
        counts: dict[str, dict[str, int]] = {}
        by_cap = body.get("by_capability")
        if isinstance(by_cap, dict):
            for name, v in by_cap.items():
                if name in _WRITE_CAPABILITIES:
                    key = name
                elif isinstance(name, str) and name.startswith("appointments."):
                    key = "appointments"
                else:
                    continue
                v = v if isinstance(v, dict) else {}
                slot = counts.setdefault(key, {"ok": 0, "error": 0})
                for field in ("ok", "error"):
                    n = v.get(field)
                    slot[field] += n if isinstance(n, int) and not isinstance(n, bool) else 0
        return {"window_hours": window, "counts": counts}


def get_backend(business: str | None = None, sender: str | None = None) -> Backend:
    if business == "kedd-demo":
        # The public demo's live business: scoped credential + demo key id. The main key
        # is never consulted here, so a public deploy cannot escalate by misconfiguration.
        if not (settings.kedd_api_url and settings.kedd_demo_secret and settings.kedd_demo_phone):
            raise RuntimeError("KEDD demo bağlantısı bu ortamda kapalı "
                               "(KEDD_API_URL / KEDD_DEMO_SECRET / KEDD_DEMO_PHONE yok)")
        return KeddBackend(settings.kedd_api_url, settings.kedd_demo_secret,
                           settings.kedd_demo_phone, key_id="demo")
    if business == "kedd":
        # Per-conversation KEDD mode (/kedd): the merchant is identified by the WhatsApp
        # sender of this session, never by a model-chosen value.
        if not (settings.kedd_api_url and settings.kedd_agent_secret):
            raise RuntimeError("KEDD bağlantısı bu ortamda kapalı (KEDD_API_URL / KEDD_AGENT_SECRET yok)")
        if not sender:
            raise RuntimeError("KEDD bağlantısı için gönderen numarası yok")
        return KeddBackend(settings.kedd_api_url, settings.kedd_agent_secret, "+" + sender.lstrip("+"))
    if settings.backend == "kedd":
        if not (settings.kedd_api_url and settings.kedd_agent_secret and settings.merchant_phone):
            raise RuntimeError("BACKEND=kedd needs KEDD_API_URL, KEDD_AGENT_SECRET and MERCHANT_PHONE")
        return KeddBackend(settings.kedd_api_url, settings.kedd_agent_secret, settings.merchant_phone)
    return MockBackend(business=business)
