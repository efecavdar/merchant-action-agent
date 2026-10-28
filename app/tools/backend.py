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

import hashlib
import hmac
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
    status: str = "scheduled"
    # Engagement is SIMULATED in mock mode and clearly labelled as such in reports.
    replies: int = 0
    conversions: int = 0
    day: str = ""                       # ISO date the campaign was sent


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

    # write (only the executor calls these, after human approval)
    def add_catalog_item(self, item_type: str, name: str, price: float, category: str,
                         description: str, image_path: str = "",
                         duration_minutes: int | None = None) -> dict[str, Any]: ...
    def create_discount(self, name: str, percent: int, item_ids: list[str],
                        starts: str, ends: str) -> dict[str, Any]: ...
    def send_campaign(self, name: str, segment_id: str, message: str,
                      visual_path: str) -> dict[str, Any]: ...
    def create_appointment(self, customer_id: str, item_id: str, staff: str,
                           starts: str) -> dict[str, Any]: ...
    def reschedule_appointment(self, appointment_id: str, starts: str,
                               staff: str | None = None) -> dict[str, Any]: ...
    def cancel_appointment(self, appointment_id: str, reason: str = "") -> dict[str, Any]: ...
    def apply_bulk_price(self, preview_id: str) -> dict[str, Any]: ...
    def adjust_loyalty(self, customer_id: str, points: int, reason: str) -> dict[str, Any]: ...
    def ads_action(self, campaign_id: str, action: str,
                   delta_pct: int | None = None) -> dict[str, Any]: ...
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
    SCHEMA = 3

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

    def send_campaign(self, name: str, segment_id: str, message: str,
                      visual_path: str) -> dict[str, Any]:
        if segment_id not in self.segments:
            raise ValueError(f"unknown segment {segment_id}")
        n = len(self.segments[segment_id])
        if n > settings.max_campaign_recipients:
            raise ValueError(f"segment too large ({n} > {settings.max_campaign_recipients})")
        c = Campaign(self._next_id("cmp"), name, segment_id, message, visual_path, n, status="sent",
                     day=self.today.isoformat())
        # Simulated engagement so the report has numbers to show. Labelled "simulated".
        c.replies = int(n * self._rng.uniform(0.18, 0.32))
        c.conversions = int(c.replies * self._rng.uniform(0.25, 0.45))
        self.campaigns.append(c)
        return {**asdict(c), "note": "mock backend: messages are NOT sent; engagement is simulated"}

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
            "appointments_changed": self.appointment_changes,
            "engagement_note": "simulated (mock backend)",
        }


# --------------------------------------------------------------------------- #
# KEDD adapter (private platform; HTTP only, no KEDD source code here)
# --------------------------------------------------------------------------- #

class KeddBackend:
    """Adapter onto KEDD's agent gateway (a handful of signed HTTPS endpoints).

    Security contract (enforced on the KEDD side, mirrored here):
      * every request carries the merchant's WhatsApp phone, a timestamp and an
        HMAC-SHA256 signature over "<timestamp>.<method>.<path?query>.<body>"; the gateway
        resolves the business from its own phone→business mapping, never from the client
      * writes carry an Idempotency-Key so a retry can never send a campaign twice
      * customer phone numbers never leave KEDD: segments come back as counts + samples
    The platform itself is proprietary and not included; with BACKEND=mock nothing here runs.
    """

    name = "kedd"

    def __init__(self, base_url: str, secret: str, merchant_phone: str):
        import requests  # local import keeps mock mode dependency-light
        self._s = requests.Session()
        self.base = base_url.rstrip("/")
        self._secret = secret.encode()
        self._phone = merchant_phone

    def _headers(self, method: str, path: str, body: str) -> dict[str, str]:
        ts = str(int(time.time()))
        msg = f"{ts}.{method}.{path}.{body}".encode()
        sig = hmac.new(self._secret, msg, hashlib.sha256).hexdigest()
        return {"X-Agent-Phone": self._phone, "X-Agent-Timestamp": ts, "X-Agent-Signature": sig,
                "Content-Type": "application/json"}

    def _get(self, path: str, **params):
        # The query string is part of the signed target, exactly as sent on the wire.
        from urllib.parse import urlencode
        qs = urlencode({k: v for k, v in params.items() if v not in (None, "")})
        target = f"{path}?{qs}" if qs else path
        r = self._s.get(f"{self.base}{target}", headers=self._headers("GET", target, ""), timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict):
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        headers = {**self._headers("POST", path, payload), "Idempotency-Key": str(uuid.uuid4())}
        r = self._s.post(f"{self.base}{path}", data=payload.encode(), headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def store_profile(self): return self._get("/agent/profile")
    def list_catalog(self, item_type=None): return self._get("/agent/catalog", type=item_type or "")
    def find_customers(self, inactive_days=None, min_visits=1, tags=None):
        return self._post("/agent/segments", {"inactive_days": inactive_days,
                                              "min_visits": min_visits, "tags": tags})
    def get_schedule(self, day): return self._get("/agent/schedule", day=day)
    def find_customer(self, name): return self._get("/agent/customers", name=name)
    def daily_summary(self, day): return self._get("/agent/daily-summary", day=day)
    def find_appointments(self, customer_name=None, day=None):
        return self._get("/agent/appointments", customer_name=customer_name or "", day=day or "")
    def preview_bulk_price(self, item_type=None, category=None, item_ids=None, mode=None, value=None):
        return self._post("/agent/catalog/bulk-price/preview",
                          {"item_type": item_type, "category": category, "item_ids": item_ids,
                           "mode": mode, "value": value})
    def ads_performance(self, days=7):
        return self._get("/agent/ads/performance", days=days)
    def ads_suggestions(self):
        return self._get("/agent/ads/suggestions")
    def add_catalog_item(self, item_type, name, price, category, description, image_path="",
                         duration_minutes=None):
        return self._post("/agent/catalog", {"type": item_type, "name": name, "price": price,
                                             "category": category, "description": description,
                                             "image_path": image_path, "duration_minutes": duration_minutes})
    def create_discount(self, name, percent, item_ids, starts, ends):
        return self._post("/agent/discounts", {"name": name, "percent": percent,
                                               "item_ids": item_ids, "starts": starts, "ends": ends})
    def send_campaign(self, name, segment_id, message, visual_path):
        return self._post("/agent/campaigns", {"name": name, "segment_id": segment_id,
                                               "message": message, "visual_path": visual_path})
    def create_appointment(self, customer_id, item_id, staff, starts):
        return self._post("/agent/appointments", {"customer_id": customer_id, "item_id": item_id,
                                                  "staff": staff, "starts": starts})
    def reschedule_appointment(self, appointment_id, starts, staff=None):
        return self._post("/agent/appointments/reschedule", {"appointment_id": appointment_id,
                                                              "starts": starts, "staff": staff})
    def cancel_appointment(self, appointment_id, reason=""):
        return self._post("/agent/appointments/cancel", {"appointment_id": appointment_id, "reason": reason})
    def apply_bulk_price(self, preview_id):
        return self._post("/agent/catalog/bulk-price", {"preview_id": preview_id})
    def adjust_loyalty(self, customer_id, points, reason):
        return self._post("/agent/loyalty/adjust", {"customer_id": customer_id,
                                                    "points": points, "reason": reason})
    def ads_action(self, campaign_id, action, delta_pct=None):
        return self._post("/agent/ads/action", {"campaign_id": campaign_id, "action": action,
                                                "delta_pct": delta_pct})
    def report(self): return self._get("/agent/report")


def get_backend(business: str | None = None) -> Backend:
    if settings.backend == "kedd":
        if not (settings.kedd_api_url and settings.kedd_agent_secret and settings.merchant_phone):
            raise RuntimeError("BACKEND=kedd needs KEDD_API_URL, KEDD_AGENT_SECRET and MERCHANT_PHONE")
        return KeddBackend(settings.kedd_api_url, settings.kedd_agent_secret, settings.merchant_phone)
    return MockBackend(business=business)
