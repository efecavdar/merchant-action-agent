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

    # write (only the executor calls these, after human approval)
    def add_catalog_item(self, item_type: str, name: str, price: float, category: str,
                         description: str, image_path: str = "",
                         duration_minutes: int | None = None) -> dict[str, Any]: ...
    def create_discount(self, name: str, percent: int, item_ids: list[str],
                        starts: str, ends: str) -> dict[str, Any]: ...
    def send_campaign(self, name: str, segment_id: str, message: str,
                      visual_path: str) -> dict[str, Any]: ...
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


class MockBackend:
    """In-memory KEDD-shaped backend. State can be persisted to data/store.json for the demo."""

    name = "mock"

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
        services = [i for i in self.items if i.type == "service"]
        self.appointments: list[Appointment] = (
            _seed_appointments(rng, self.today, self.customers, services, self.profile["staff"])
            if services and self.profile["staff"] else []
        )
        self.discounts: list[Discount] = []
        self.campaigns: list[Campaign] = []
        self.segments: dict[str, list[str]] = {}
        self._rng = random.Random(seed + 1)
        self._counter = 100
        self._items_at_start = len(self.items)

    # -- helpers ------------------------------------------------------------- #
    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"

    def _item(self, item_id: str) -> CatalogItem:
        for i in self.items:
            if i.id == item_id:
                return i
        raise ValueError(f"unknown catalog item {item_id}")

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

    # -- write tools (only called by the executor AFTER human approval) ------ #
    def add_catalog_item(self, item_type: str, name: str, price: float, category: str,
                         description: str, image_path: str = "",
                         duration_minutes: int | None = None) -> dict[str, Any]:
        if item_type not in ITEM_TYPES:
            raise ValueError(f"item_type must be one of {ITEM_TYPES}")
        it = CatalogItem(self._next_id("i"), item_type, name, float(price), "TRY", category,
                         description, image_path, duration_minutes=duration_minutes)
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
        c = Campaign(self._next_id("cmp"), name, segment_id, message, visual_path, n, status="sent")
        # Simulated engagement so the report has numbers to show. Labelled "simulated".
        c.replies = int(n * self._rng.uniform(0.18, 0.32))
        c.conversions = int(c.replies * self._rng.uniform(0.25, 0.45))
        self.campaigns.append(c)
        return {**asdict(c), "note": "mock backend: messages are NOT sent; engagement is simulated"}

    def report(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "business": self.profile["business_name"],
            "items_added": [asdict(i) for i in self.items[self._items_at_start:]],
            "active_discounts": [asdict(d) for d in self.discounts],
            "campaigns": [asdict(c) for c in self.campaigns],
            "engagement_note": "simulated (mock backend)",
        }


# --------------------------------------------------------------------------- #
# KEDD adapter (private platform; HTTP only, no KEDD source code here)
# --------------------------------------------------------------------------- #

class KeddBackend:
    """Adapter onto KEDD's agent gateway (a handful of signed HTTPS endpoints).

    Security contract (enforced on the KEDD side, mirrored here):
      * every request carries the merchant's WhatsApp phone, a timestamp and an
        HMAC-SHA256 signature over "<timestamp>.<method>.<path>.<body>"; the gateway
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
        r = self._s.get(f"{self.base}{path}", params=params, headers=self._headers("GET", path, ""), timeout=30)
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
    def report(self): return self._get("/agent/report")


def get_backend(business: str | None = None) -> Backend:
    if settings.backend == "kedd":
        if not (settings.kedd_api_url and settings.kedd_agent_secret and settings.merchant_phone):
            raise RuntimeError("BACKEND=kedd needs KEDD_API_URL, KEDD_AGENT_SECRET and MERCHANT_PHONE")
        return KeddBackend(settings.kedd_api_url, settings.kedd_agent_secret, settings.merchant_phone)
    return MockBackend(business=business)
