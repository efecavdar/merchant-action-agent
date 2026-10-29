"""KeddBackend shape parity: every method returns its MockBackend shape (task 11).

Each case feeds a sanitized gateway fixture (tests/fixtures/kedd/*.json, synthetic values
only) through KeddBackend with mocked HTTP, then compares the result against the same call
on MockBackend: identical keys (recursively), identical leaf types — except that fields the
gateway cannot provide come back as None (scalars) or [] (lists), which is the documented
gap encoding (see the task-11 PR description). No result may contain a phone number.
"""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.tools.backend import KeddBackend, MockBackend

FIX = Path(__file__).parent / "fixtures" / "kedd"
DAY0 = date(2026, 10, 27)
BASE = "https://kedd.example"


def _load(name: str):
    return json.loads((FIX / f"{name}.json").read_text())


class _RoutingSession:
    """Serve one fixture per (method, gateway path); record traffic. No network."""

    def __init__(self, routes: dict[tuple[str, str], object]):
        self.routes = routes
        self.calls: list[tuple] = []

    def _resp(self, payload):
        status, body = payload if isinstance(payload, tuple) else (200, payload)
        return SimpleNamespace(json=lambda: body, status_code=status)

    def _call(self, method, url, headers=None, data=None):
        path = url[len(BASE):].split("?")[0]
        body = json.loads(data) if data else None
        self.calls.append((method, url, headers, body))
        return self._resp(self.routes[(method, path)])

    def get(self, url, headers=None, timeout=None):
        return self._call("GET", url, headers)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._call("POST", url, headers, data)


def _kb(*fixtures: str, **overrides) -> KeddBackend:
    routes = {}
    for name in fixtures:
        method = "POST" if name in {"segments", "bulk_preview", "catalog_create", "bulk_apply",
                                    "discounts", "appointment_create", "appointment_reschedule",
                                    "appointment_cancel", "social_post"} else "GET"
        routes[(method, _PATHS[name])] = _load(name)
    routes.update(overrides)
    kb = KeddBackend(BASE, "s3cret", "+905321")
    kb._s = _RoutingSession(routes)
    return kb


_PATHS = {
    "profile": "/agent/profile",
    "catalog": "/agent/catalog",
    "segments": "/agent/segments",
    "schedule": "/agent/schedule",
    "customers": "/agent/customers",
    "daily_summary": "/agent/daily-summary",
    "appointments": "/agent/appointments",
    "bulk_preview": "/agent/catalog/bulk-price/preview",
    "ads_performance": "/agent/ads/performance",
    "ads_suggestions": "/agent/ads/suggestions",
    "report": "/agent/report",
    "campaign_templates": "/agent/campaign-templates",
    "catalog_create": "/agent/catalog",
    "bulk_apply": "/agent/catalog/bulk-price",
    "discounts": "/agent/discounts",
    "appointment_create": "/agent/appointments",
    "appointment_reschedule": "/agent/appointments/reschedule",
    "appointment_cancel": "/agent/appointments/cancel",
    "social_post": "/agent/social/post",
}


def _tiny_png() -> str:
    import tempfile

    from PIL import Image
    p = Path(tempfile.gettempdir()) / "cirak-qa-tiny.png"
    Image.new("RGB", (8, 8)).save(p)
    return str(p)


def _mock() -> MockBackend:
    return MockBackend("salon", today=DAY0)


_KEY_AGNOSTIC = {"result.free_slots"}  # mock keys by staff; flat gateway slots land under "-"


def _assert_same_shape(mock_v, kedd_v, path="result"):
    """Same keys recursively; leaf types equal unless either side reports None.

    None on the adapter side is the documented gap encoding (the gateway cannot
    provide this field); None on the mock side is an empty optional (sale_price,
    sale window, product duration). Both mean "absent", so both pass.
    """
    if kedd_v is None or mock_v is None:
        return
    if path in _KEY_AGNOSTIC and isinstance(mock_v, dict):
        assert isinstance(kedd_v, dict) and kedd_v and mock_v, path
        first = next(iter(mock_v.values()))
        for k, v in kedd_v.items():
            _assert_same_shape(first, v, f"{path}.{k}")
        return
    if isinstance(mock_v, dict):
        assert isinstance(kedd_v, dict), path
        assert set(kedd_v) == set(mock_v), f"{path}: keys {sorted(kedd_v)} != {sorted(mock_v)}"
        for k in mock_v:
            _assert_same_shape(mock_v[k], kedd_v[k], f"{path}.{k}")
    elif isinstance(mock_v, list):
        assert isinstance(kedd_v, list), path
        for i in range(min(len(mock_v), len(kedd_v))):
            _assert_same_shape(mock_v[i], kedd_v[i], f"{path}[{i}]")
        # Lengths legitimately differ (fixture vs seed); empty kedd lists are documented gaps.
    else:
        assert type(kedd_v) is type(mock_v), f"{path}: {type(kedd_v)} != {type(mock_v)}"


def _dig(obj, dotted: str):
    for part in dotted.split("."):
        obj = obj[part]
    return obj


def _first_service(m: MockBackend) -> str:
    return next(i.id for i in m.items if i.type == "service")


CASES = [
    # (name, fixtures, mock call, kedd call, paths that must be non-empty)
    ("store_profile", ["profile", "catalog"],
     lambda m: m.store_profile(), lambda k: k.store_profile(), ["staff"]),
    ("list_catalog", ["catalog"],
     lambda m: m.list_catalog(), lambda k: k.list_catalog(), [""]),
    ("list_catalog_filtered", ["catalog"],
     lambda m: m.list_catalog("service"), lambda k: k.list_catalog("service"), [""]),
    ("find_customers", ["segments"],
     lambda m: m.find_customers(inactive_days=60), lambda k: k.find_customers(inactive_days=60),
     ["sample"]),
    ("get_schedule", ["schedule", "profile"],
     lambda m: m.get_schedule("2026-10-29"), lambda k: k.get_schedule("2026-10-29"),
     ["appointments", "free_slots.-"]),
    ("find_customer", ["customers"],
     lambda m: m.find_customer(m.customers[0].name.split()[0]),
     lambda k: k.find_customer("Ayşe"), [""]),
    ("daily_summary", ["daily_summary"],
     lambda m: m.daily_summary("2026-10-27"), lambda k: k.daily_summary("2026-10-27"), []),
    ("find_appointments", ["appointments"],
     lambda m: m.find_appointments(), lambda k: k.find_appointments(), [""]),
    ("preview_bulk_price", ["bulk_preview"],
     lambda m: m.preview_bulk_price(mode="percent", value=10),
     lambda k: k.preview_bulk_price(mode="percent", value=10), ["items"]),
    ("ads_performance", ["ads_performance"],
     lambda m: m.ads_performance(7), lambda k: k.ads_performance(7), ["campaigns"]),
    ("ads_suggestions", ["ads_suggestions"],
     lambda m: m.ads_suggestions(), lambda k: k.ads_suggestions(), ["suggestions"]),
    ("campaign_templates", ["campaign_templates"],
     lambda m: m.campaign_templates(), lambda k: k.campaign_templates(), [""]),
    ("add_catalog_item", ["catalog_create"],
     lambda m: m.add_catalog_item("product", "Fular", 850.0, "aksesuar", "İpek fular"),
     lambda k: k.add_catalog_item("product", "Fular", 850.0, "aksesuar", "İpek fular"), []),
    ("create_discount", ["discounts"],
     lambda m: m.create_discount("Test", 15, [m.items[0].id], "2026-10-31", "2026-11-07"),
     lambda k, iid=None: k.create_discount("Test", 15, [iid], "2026-10-31", "2026-11-07"),
     ["sale_prices"]),
    ("create_appointment", ["profile", "appointment_create"],
     lambda m: m.create_appointment(m.customers[0].id, _first_service(m), "Elif", "2026-12-24T15:00"),
     lambda k: k.create_appointment("c-1", "item-1", "Elif", "2026-12-24T15:00"), []),
    ("reschedule_appointment", ["appointment_reschedule"],
     lambda m: m.reschedule_appointment(m.appointments[0].id, "2026-12-24T11:00"),
     lambda k: k.reschedule_appointment("appt-1", "2026-12-24T11:00"), []),
    ("cancel_appointment", ["appointment_cancel"],
     lambda m: m.cancel_appointment(m.appointments[0].id),
     lambda k: k.cancel_appointment("appt-1"), []),
    ("apply_bulk_price", ["bulk_apply"],
     lambda m: m.apply_bulk_price(m.preview_bulk_price(mode="percent", value=10)["preview_id"]),
     lambda k: k.apply_bulk_price("pv-fixture-1"), []),
    ("report", ["report", "profile"],
     lambda m: m.report(), lambda k: k.report(), []),
    ("publish_social_post", ["social_post"],
     lambda m: m.publish_social_post("Yeni #sezon", _tiny_png(), ["instagram"], "2026-11-01T10:00"),
     lambda k: k.publish_social_post("Yeni #sezon", _tiny_png(), ["instagram"], "2026-11-01T10:00"),
     ["campaign_id"]),
]


@pytest.mark.parametrize("name,fixtures,mock_call,kedd_call,nonempty", CASES)
def test_shape_parity(name, fixtures, mock_call, kedd_call, nonempty):
    mock_backend = _mock()
    if name == "create_discount":
        kedd_res = kedd_call(_kb(*fixtures), mock_backend.items[0].id)
    else:
        kedd_res = kedd_call(_kb(*fixtures))
    mock_res = mock_call(mock_backend)
    _assert_same_shape(mock_res, kedd_res)
    for p in nonempty:
        target = kedd_res if p == "" else _dig(kedd_res, p)
        assert len(target) > 0, f"{name}: {p or 'result'} is empty"
    blob = json.dumps(kedd_res, ensure_ascii=False)
    assert "+90" not in blob and '"phone' not in blob


# --------------------------------------------------------------------------- #
# request mapping + gateway limits (the adapter must not silently reinterpret)
# --------------------------------------------------------------------------- #

def test_create_splits_starts_and_resolves_staff_id():
    kb = _kb("profile", "appointment_create")
    kb.create_appointment("c-1", "item-1", "Deniz", "2026-12-24T15:00")
    posts = [c for c in kb._s.calls if c[0] == "POST"]
    assert posts[0][1] == f"{BASE}/agent/appointments"
    assert posts[0][3] == {"customer_id": "c-1", "catalog_item_id": "item-1", "date": "2026-12-24",
                           "time": "15:00", "staff_id": "st-2"}
    assert any(c[0] == "GET" and c[1] == f"{BASE}/agent/profile" for c in kb._s.calls)


def test_create_rejects_unknown_staff_and_bad_dates():
    kb = _kb("profile", "appointment_create")
    with pytest.raises(ValueError, match="Çalışan bulunamadı: Kerem"):
        kb.create_appointment("c-1", "item-1", "Kerem", "2026-12-24T15:00")
    with pytest.raises(ValueError, match="Tarih anlaşılmadı"):
        kb.create_appointment("c-1", "item-1", "Elif", "yarın öğlen")


def test_preview_posts_percent_only():
    kb = _kb("bulk_preview")
    kb.preview_bulk_price(category="kesim", mode="percent", value=10)
    assert kb._s.calls[0][3] == {"percent": 10, "category": "kesim"}
    with pytest.raises(ValueError, match="yalnız yüzde"):
        kb.preview_bulk_price(mode="amount", value=50)
    with pytest.raises(ValueError, match="kalem seçmez"):
        kb.preview_bulk_price(item_ids=["item-1"], mode="percent", value=10)


def test_discount_posts_once_per_item_with_stable_keys():
    kb = _kb("discounts")
    res = kb.create_discount("Test", 15, ["item-1", "item-2"], "2026-10-31", "2026-11-07",
                             idempotency_key="run-3")
    posts = [c for c in kb._s.calls if c[0] == "POST"]
    assert [p[3]["item_id"] for p in posts] == ["item-1", "item-2"]
    assert [p[2]["Idempotency-Key"] for p in posts] == ["run-3#0", "run-3#1"]
    assert posts[0][3]["start_day"] == "2026-10-31" and posts[0][3]["percent"] == 15
    assert set(res["sale_prices"]) == {"item-1", "item-2"}
    kb2 = _kb("discounts")
    kb2.create_discount("Test", 15, ["item-1"], "2026-10-31", "2026-11-07", idempotency_key="run-4")
    assert kb2._s.calls[0][2]["Idempotency-Key"] == "run-4"  # single item keeps the exact key


def test_reschedule_posts_date_time_and_rejects_staff_change():
    kb = _kb("appointment_reschedule")
    kb.reschedule_appointment("appt-1", "2026-12-24T11:00")
    assert kb._s.calls[0][3] == {"appointment_id": "appt-1", "date": "2026-12-24", "time": "11:00"}
    with pytest.raises(ValueError, match="personeli değiştiremez"):
        kb.reschedule_appointment("appt-1", "2026-12-24T11:00", staff="Deniz")


def test_cancel_posts_id_only():
    kb = _kb("appointment_cancel")
    kb.cancel_appointment("appt-1", reason="müşteri isteği")
    assert kb._s.calls[0][3] == {"appointment_id": "appt-1"}


@pytest.mark.parametrize("call", [
    lambda k: k.send_campaign("Kış", "seg9", template="marketing_reengagement_promo_tr",
                              variables={"2": "X"}),
    lambda k: k.send_campaign("Eski", "seg9", "merhaba", ""),
    lambda k: k.adjust_loyalty("c-1", 100, "doğum günü"),
    lambda k: k.ads_action("ad-1", "pause"),
])
def test_unimplemented_writes_raise_before_any_http(call):
    kb = _kb()
    with pytest.raises(ValueError, match="henüz bağlı değil"):
        call(kb)
    assert kb._s.calls == []


def test_schedule_gaps_come_from_flat_slots_and_cancelled_is_dropped():
    res = _kb("schedule", "profile").get_schedule("2026-10-29")
    assert res["free_slots"] == {"-": [{"from": "09:30", "to": "10:30"},
                                       {"from": "11:00", "to": "11:30"}]}
    assert len(res["appointments"]) == 1 and res["appointments"][0]["customer"] == "Ayşe Y."
    assert res["appointments"][0]["time"] == "10:00–10:45"  # UTC fixture → TR wall clock
    assert res["business_hours"] == {"open": "09:00", "close": "18:00"}


def test_null_fallbacks_keep_mock_keys():
    appts = _kb("appointments").find_appointments()
    assert appts[1] == {"id": "appt-2", "customer": "", "service": "?", "staff": "",
                        "starts": None, "ends": None, "status": "completed"}
    items = _kb("catalog").list_catalog()
    assert items[2]["price"] is None and items[2]["category"] == ""


def test_profile_counts_survive_a_missing_catalog_module():
    kb = _kb("profile")
    kb._s.routes[("GET", "/agent/catalog")] = (403, {"error": "forbidden"})
    res = kb.store_profile()
    assert res["catalog_count"] == {"product": None, "service": None}
    assert res["business_name"] == "Örnek Kuaför" and res["staff"] == ["Elif", "Deniz"]


# --------------------------------------------------------------------------- #
# task 14: social post upload
# --------------------------------------------------------------------------- #

def test_t14_social_post_body_shape_and_stable_key(tmp_path):
    import base64

    from PIL import Image
    img = tmp_path / "post.png"
    Image.new("RGB", (64, 64), (200, 30, 40)).save(img)
    kb = _kb("social_post")
    res = kb.publish_social_post("Yeni #sezon", str(img), ["instagram", "facebook"],
                                 "2026-11-01T10:00", idempotency_key="run-9-0")
    method, url, headers, body = kb._s.calls[0]
    assert (method, url) == ("POST", BASE + "/agent/social/post")
    assert headers["Idempotency-Key"] == "run-9-0"
    assert body["caption"] == "Yeni #sezon" and body["platforms"] == ["instagram", "facebook"]
    assert body["publish_at"] == "2026-11-01T10:00"
    raw = base64.b64decode(body["image_base64"])
    assert raw[:2] == b"\xff\xd8" and len(raw) <= 5 * 1024 * 1024  # jpg, ≤ 5 MB
    assert res["campaign_id"] == "sp-9" and res["status"] == "scheduled"
    # same key on retry: the gateway answers from its stored result
    kb.publish_social_post("Yeni #sezon", str(img), ["instagram"], idempotency_key="run-9-0")
    assert kb._s.calls[1][2]["Idempotency-Key"] == "run-9-0"
    assert "publish_at" not in kb._s.calls[1][3]  # absent, not null


def test_t14_social_post_downscales_huge_images(tmp_path):
    import base64
    import io

    from PIL import Image
    # dense noise: ~6.6 MB as JPEG q85, ~5.5 MB as q75 — only rescaling fits 5 MB
    big = tmp_path / "big.png"
    Image.effect_noise((3000, 3000), 128).convert("RGB").save(big)
    kb = _kb("social_post")
    kb.publish_social_post("büyük", str(big), ["instagram"], idempotency_key="k")
    raw = base64.b64decode(kb._s.calls[0][3]["image_base64"])
    assert raw[:2] == b"\xff\xd8" and len(raw) <= 5 * 1024 * 1024
    assert Image.open(io.BytesIO(raw)).size < (3000, 3000)
    with pytest.raises(ValueError, match="okunamadı"):
        kb.publish_social_post("yok", str(tmp_path / "missing.png"), ["instagram"])
