"""Offline tests: everything that does not need an API key."""

import json
import pickle
import unicodedata
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from PIL import Image

from app.actions import validate_proposal
from app.config import settings
from app.meta_client import extract_json, parse_sam_boxes
from app.pipeline import MediaContext, ProductCard
from app.tools.backend import KeddBackend, MockBackend
from app import agent, approval, executor


TODAY = date(2026, 10, 27)

SAM_LINE = ("<0f>0<|box;x1=353;y1=382;x2=453;y2=427;w=537;h=561;c=0.96|><|mask;x=0;y=0;c=0.96;data=46,101,!abc|>,"
            "1<|box;x1=260;y1=382;x2=340;y2=427;w=537;h=561|><|mask;x=0;y=0;data=46,81,~xyz|>")


def test_parse_sam_boxes():
    boxes = parse_sam_boxes(SAM_LINE)
    assert [b.object_id for b in boxes] == [0, 1]
    assert boxes[0].confidence == 0.96 and boxes[1].confidence is None
    assert boxes[0].frame_w == 537 and boxes[1].x2 == 340


def test_extract_json_tolerates_fences():
    assert extract_json('```json\n{"a": 1}\n```')["a"] == 1
    assert extract_json('Sure: {"name": "Çanta"} done')["name"] == "Çanta"


# --------------------------------------------------------------------------- #
# mock backend (KEDD-shaped)
# --------------------------------------------------------------------------- #

def test_mock_butik_segments_discount_report(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    seg = b.find_customers(inactive_days=60)
    assert seg["count"] > 0 and seg["segment_id"] in b.segments
    assert seg["excluded_no_consent"] >= 0 and "phone" not in str(seg)   # phones never leave the backend
    p = b.add_catalog_item("product", "Deri Çanta", 850, "çanta", "desc", "x.png")
    d = b.create_discount("hafta sonu", 15, [p["id"]], "2026-10-31", "2026-11-01")
    assert d["sale_prices"][p["id"]] == 722.5                             # KEDD-style sale price window
    c = b.send_campaign("k", seg["segment_id"], "merhaba", "")
    assert c["recipients"] == seg["count"] and d["percent"] == 15
    rep = b.report()
    assert rep["engagement_note"].startswith("simulated") and rep["items_added"][0]["name"] == "Deri Çanta"
    with pytest.raises(ValueError):
        b.create_discount("x", 10, ["nope"], "2026-10-31", "2026-11-01")


def test_mock_salon_services_and_schedule(tmp_path):
    b = MockBackend("salon", today=TODAY, store_path=tmp_path / "s.json")
    services = b.list_catalog("service")
    assert services and all(s["duration_minutes"] for s in services)
    assert b.list_catalog("product") == []
    s = b.add_catalog_item("service", "Keratin Bakımı XL", 2500, "bakım", "d", duration_minutes=90)
    assert s["type"] == "service" and s["duration_minutes"] == 90
    sched = b.get_schedule("2026-10-28")
    assert set(sched["free_slots"]) == {"Elif", "Deniz"}
    assert sched["appointments"] and all("customer" in a for a in sched["appointments"])
    # free slots never overlap appointments of the same staff member
    for staff, gaps in sched["free_slots"].items():
        busy = [a["time"].split("–") for a in sched["appointments"] if a["staff"] == staff]
        for g in gaps:
            assert all(g["to"] <= st or g["from"] >= en for st, en in busy)
    assert b.store_profile()["catalog_count"] == {"product": 0, "service": 6}


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #

CATALOG = {"i001": {"id": "i001", "price": 650.0}}


def _proposal(seg_id):
    return {
        "summary_tr": "1 çanta eklenecek, hafta sonu %15, 20 müşteriye mesaj.",
        "actions": [
            {"type": "add_catalog_item", "item_type": "product", "temp_id": "tmp1", "name": "Deri Çanta", "price": 850},
            {"type": "create_discount", "percent": 15, "item_refs": ["tmp1"], "starts": "2026-10-31", "ends": "2026-11-01"},
            {"type": "generate_visual", "visual_prompt": "leather bag", "item_refs": ["tmp1"]},
            {"type": "send_campaign", "segment_id": seg_id, "message": "Selam! Çanta 850 TL, hafta sonu %15 🎁",
             "attach_visual": True},
        ],
    }


def test_validate_proposal_ok_and_errors():
    assert validate_proposal(_proposal("seg101"), {"tmp1"}, CATALOG, {"seg101"}) == []
    bad = _proposal("seg999")
    bad["actions"][1]["percent"] = 90
    errs = validate_proposal(bad, {"tmp1"}, CATALOG, {"seg101"})
    assert any("percent" in e for e in errs) and any("segment_id" in e for e in errs)


def test_validate_text_only_item_and_service():
    prop = {"summary_tr": "ok", "actions": [
        {"type": "add_catalog_item", "item_type": "service", "ref": "new1", "name": "Keratin", "price": 2500,
         "duration_minutes": 90},
        {"type": "create_discount", "percent": 20, "item_refs": ["new1", "i001"], "starts": "2026-10-28",
         "ends": "2026-10-28"},
    ]}
    assert validate_proposal(prop, set(), CATALOG, set()) == []
    prop["actions"][0]["duration_minutes"] = 2
    assert any("duration" in e for e in validate_proposal(prop, set(), CATALOG, set()))
    del prop["actions"][0]["ref"]
    assert any("temp_id" in e and "ref" in e for e in validate_proposal(prop, set(), CATALOG, set()))


def test_validate_message_must_match_plan():
    # the failure seen in the first live run: discount on unrelated items, message promises a new product at 850
    prop = {"summary_tr": "ok", "actions": [
        {"type": "create_discount", "percent": 15, "item_refs": ["i001"], "starts": "2026-10-31", "ends": "2026-11-01"},
        {"type": "send_campaign", "segment_id": "seg1",
         "message": "Yeni fular koleksiyonu 850 TL, hafta sonu %15 indirim!"},
    ]}
    errs = validate_proposal(prop, set(), CATALOG, {"seg1"})
    assert len(errs) == 1 and "850" in errs[0]
    prop["actions"][1]["message"] = "Keten gömlek hafta sonu %15 indirimli: 552 TL yerine 650 TL değil, 552,5 ₺!"
    errs = validate_proposal(prop, set(), CATALOG, {"seg1"})
    assert errs == [] or all("552,5" not in e for e in errs)
    prop["actions"][1]["message"] = "Hafta sonu yüzde 30 indirim!"
    assert any("%30" in e for e in validate_proposal(prop, set(), CATALOG, {"seg1"}))


def test_validate_appointment_actions_need_ids_from_tools():
    cat = {"i001": {"id": "i001", "price": 450.0, "type": "service"}, "i009": {"id": "i009", "price": 1, "type": "product"}}
    prop = {"summary_tr": "Randevu.", "actions": [
        {"type": "create_appointment", "customer_id": "c001", "item_ref": "i001", "staff": "Elif",
         "starts": "2026-10-31T15:00", "label": "Ayşe — Saç Kesimi, Elif, Cuma 15:00"},
        {"type": "reschedule_appointment", "appointment_id": "a007", "starts": "2026-10-31T16:00", "label": "x"},
        {"type": "cancel_appointment", "appointment_id": "a008", "label": "y"},
    ]}
    assert validate_proposal(prop, set(), cat, set(), {"a007", "a008"}, {"c001"}) == []
    errs = validate_proposal(prop, set(), cat, set(), set(), set())          # ids not resolved via tools
    assert sum("customer_id" in e for e in errs) == 1 and sum("appointment_id" in e for e in errs) == 2
    prop["actions"][0]["item_ref"] = "i009"
    prop["actions"][0]["starts"] = "cuma 15"
    del prop["actions"][2]["label"]
    errs = validate_proposal(prop, set(), cat, set(), {"a007", "a008"}, {"c001"})
    assert any("not a service" in e for e in errs) and any("ISO datetime" in e for e in errs)
    assert any("label" in e for e in errs)


def test_validate_coerces_numeric_strings_and_requires_promised_campaign():
    # seen live: percent / price arrive as strings; summary promises a campaign the plan does not contain
    prop = {"summary_tr": "Hafta sonu indirim, 28 kişiye kampanya hazırladım.", "actions": [
        {"type": "add_catalog_item", "item_type": "service", "ref": "n1", "name": "Keratin", "price": "2500",
         "duration_minutes": "90"},
        {"type": "create_discount", "percent": "20", "item_refs": ["n1"], "starts": "2026-10-28", "ends": "2026-10-28"},
    ]}
    errs = validate_proposal(prop, set(), CATALOG, {"seg1"})
    assert len(errs) == 1 and "send_campaign" in errs[0]
    assert prop["actions"][0]["price"] == 2500.0 and prop["actions"][0]["duration_minutes"] == 90
    assert prop["actions"][1]["percent"] == 20
    prop["summary_tr"] = "Keratin eklendi, %20 indirim tanımlandı."
    assert validate_proposal(prop, set(), CATALOG, {"seg1"}) == []
    prop["summary_tr"] = "Keratin eklendi. Fiyat belirsiz olduğu için müşterilere mesaj göndermedim."
    assert validate_proposal(prop, set(), CATALOG, {"seg1"}) == []          # negated → no campaign expected
    assert validate_proposal(prop, set(), CATALOG, set()) == []          # no segment built → no campaign expected


# --------------------------------------------------------------------------- #
# executor + card
# --------------------------------------------------------------------------- #

def test_executor_mock_photo_and_text_items(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    seg = b.find_customers(inactive_days=60)["segment_id"]
    crop = tmp_path / "tmp1.png"
    Image.new("RGB", (10, 10)).save(crop)
    ctx = MediaContext(instruction="x", instruction_source="text",
                       products=[ProductCard("tmp1", str(crop), "src", "Çanta", "çanta", "siyah", "deri", "d")])
    prop = _proposal(seg)
    prop["actions"].insert(1, {"type": "add_catalog_item", "item_type": "product", "ref": "new1",
                               "name": "İpek Fular", "price": 850})
    prop["actions"][2]["item_refs"] = ["tmp1", "new1"]
    prop["actions"] = [a for a in prop["actions"] if a["type"] != "generate_visual"]  # no API in tests
    prop["actions"][-1]["attach_visual"] = False
    steps = executor.execute(prop, ctx, b, out_dir=tmp_path)
    assert [s["status"] for s in steps] == ["ok", "ok", "ok", "ok"]
    assert steps[0]["result"]["from_photo"] is True and steps[1]["result"]["from_photo"] is False
    assert len(steps[2]["result"]["sale_prices"]) == 2
    card = approval.render_card(prop, ctx, {seg: 20})
    assert "850" in card and "20 müşteriye" in card and "📷" in card and "İpek Fular" in card


def test_executor_fails_closed(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    ctx = MediaContext(instruction="x", instruction_source="text")
    prop = {"summary_tr": "x", "actions": [
        {"type": "create_discount", "percent": 10, "item_refs": ["ghost"], "starts": "2026-10-31", "ends": "2026-11-01"},
        {"type": "send_campaign", "segment_id": "seg1", "message": "m"},
    ]}
    steps = executor.execute(prop, ctx, b, out_dir=tmp_path)
    assert [s["status"] for s in steps] == ["failed"] and b.campaigns == []


# --------------------------------------------------------------------------- #
# daily summary + appointments
# --------------------------------------------------------------------------- #

def _salon(tmp_path, name="s.json"):
    return MockBackend("salon", today=TODAY, store_path=tmp_path / name)


def _strip_tr(s: str) -> str:
    """Fold a name to plain ASCII the way a merchant would type it."""
    nfd = unicodedata.normalize("NFD", s.lower())
    no_marks = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return no_marks.translate(str.maketrans("çğıöşü", "cgiosu"))


def test_daily_summary_deterministic_both_seeds(tmp_path):
    for business in ("butik", "salon"):
        a = MockBackend(business, today=TODAY, store_path=tmp_path / f"{business}-a.json")
        b = MockBackend(business, today=TODAY, store_path=tmp_path / f"{business}-b.json")
        assert a.daily_summary("2026-10-27") == b.daily_summary("2026-10-27")
    s = _salon(tmp_path).daily_summary("2026-10-27")
    assert s["appointments"]["upcoming"] == s["appointments"]["count"] > 0
    assert s["revenue"]["total"] > 0
    assert round(sum(s["revenue"]["by_category"].values()), 2) == s["revenue"]["total"]
    assert len(s["unanswered_messages"]["messages"]) <= 3
    assert s["unanswered_messages"]["count"] >= len(s["unanswered_messages"]["messages"])
    assert set(s["campaigns_sent"]) == {"count", "recipients"}
    shop = MockBackend("butik", today=TODAY, store_path=tmp_path / "b.json").daily_summary("2026-10-27")
    assert shop["appointments"]["count"] == 0 and shop["revenue"]["total"] > 0
    assert shop["unanswered_messages"]["count"] > 0
    with pytest.raises(ValueError):
        _salon(tmp_path).daily_summary("not-a-day")


def test_find_appointments_turkish_name_match(tmp_path):
    b = _salon(tmp_path)
    cust = b.customers[0]
    svc = next(i for i in b.items if i.type == "service")
    day = (TODAY + timedelta(days=5)).isoformat()
    appt = b.create_appointment(cust.id, svc.id, "Elif", f"{day}T11:00")
    query = _strip_tr(cust.name.split()[0])
    hits = b.find_appointments(query)
    assert appt["id"] in [h["id"] for h in hits]
    assert b.find_appointments(query.upper()) and b.find_appointments(query[:3])
    assert set(hits[0]) == {"id", "customer", "service", "staff", "starts", "ends", "status"}
    assert all(h["id"] != appt["id"] or h["starts"].startswith(day) for h in b.find_appointments(day=day))
    assert appt["id"] not in [h["id"] for h in b.find_appointments(day="2026-10-28")]
    assert b.find_appointments("zzz-kayıt-dışı") == []


def test_create_appointment_hours_and_conflicts(tmp_path):
    b = _salon(tmp_path)
    cust = b.customers[0].id
    fon = next(i for i in b.items if i.name == "Fön")   # 30 dk
    day = (TODAY + timedelta(days=5)).isoformat()
    ok = b.create_appointment(cust, fon.id, "Elif", f"{day}T11:00")
    assert (ok["starts"], ok["ends"], ok["status"]) == (f"{day}T11:00", f"{day}T11:30", "confirmed")
    edge = b.create_appointment(cust, fon.id, "Elif", f"{day}T18:30")  # ends exactly at closing
    assert edge["ends"] == f"{day}T19:00"
    other = b.create_appointment(cust, fon.id, "Deniz", f"{day}T11:15")  # same slot, other staff
    assert other["staff"] == "Deniz"
    with pytest.raises(ValueError, match="dolu"):
        b.create_appointment(cust, fon.id, "Elif", f"{day}T11:15")
    with pytest.raises(ValueError, match="çalışma saatleri"):
        b.create_appointment(cust, fon.id, "Elif", f"{day}T08:00")
    with pytest.raises(ValueError, match="çalışma saatleri"):
        b.create_appointment(cust, fon.id, "Elif", f"{day}T18:45")
    with pytest.raises(ValueError, match="Hizmet bulunamadı"):
        b.create_appointment(cust, "nope", "Elif", f"{day}T12:00")
    with pytest.raises(ValueError, match="Müşteri bulunamadı"):
        b.create_appointment("cx", fon.id, "Elif", f"{day}T12:00")
    with pytest.raises(ValueError, match="Çalışan bulunamadı"):
        b.create_appointment(cust, fon.id, "Hayalet", f"{day}T12:00")
    with pytest.raises(ValueError, match="anlaşılmadı"):
        b.create_appointment(cust, fon.id, "Elif", "yarın öğlen")
    shop = MockBackend("butik", today=TODAY, store_path=tmp_path / "b.json")
    with pytest.raises(ValueError, match="bir hizmet değil"):
        shop.create_appointment(shop.customers[0].id, shop.items[0].id, "Elif", f"{day}T12:00")


def test_reschedule_frees_old_slot(tmp_path):
    b = _salon(tmp_path)
    cust = b.customers[0].id
    fon = next(i for i in b.items if i.name == "Fön")
    day = (TODAY + timedelta(days=5)).isoformat()
    appt = b.create_appointment(cust, fon.id, "Elif", f"{day}T11:00")
    b.create_appointment(cust, fon.id, "Elif", f"{day}T15:00")

    def covers(gaps, frm, to):
        return any(g["from"] <= frm and to <= g["to"] for g in gaps)

    assert not covers(b.get_schedule(day)["free_slots"]["Elif"], "11:00", "11:30")
    moved = b.reschedule_appointment(appt["id"], f"{day}T14:00")
    assert moved["starts"] == f"{day}T14:00" and moved["item_id"] == fon.id
    assert moved["customer_id"] == cust and moved["staff"] == "Elif"
    free = b.get_schedule(day)["free_slots"]["Elif"]
    assert covers(free, "11:00", "11:30") and not covers(free, "14:00", "14:30")
    with pytest.raises(ValueError, match="dolu"):
        b.reschedule_appointment(appt["id"], f"{day}T15:15")
    with pytest.raises(ValueError, match="Randevu bulunamadı"):
        b.reschedule_appointment("ax", f"{day}T12:00")


def test_cancel_reflected_in_schedule_and_report(tmp_path):
    b = _salon(tmp_path)
    kesim = next(i for i in b.items if i.name == "Saç Kesimi")
    day = (TODAY + timedelta(days=6)).isoformat()
    appt = b.create_appointment(b.customers[1].id, kesim.id, "Deniz", f"{day}T10:00")
    b.cancel_appointment(appt["id"], reason="müşteri iptal etti")
    sched = b.get_schedule(day)
    assert all(a["staff"] != "Deniz" or "10:00" not in a["time"] for a in sched["appointments"])
    assert any(g["from"] == "10:00" for g in sched["free_slots"]["Deniz"])
    found = b.find_appointments(day=day)
    assert len(found) == 1 and found[0]["status"] == "cancelled"
    changes = [c for c in b.report()["appointments_changed"] if c["id"] == appt["id"]]
    assert [c["change"] for c in changes] == ["created", "cancelled"]
    assert changes[1]["reason"] == "müşteri iptal etti"
    b.cancel_appointment(appt["id"])   # idempotent: no duplicate event
    assert sum(1 for c in b.report()["appointments_changed"] if c["id"] == appt["id"]) == 2


def test_save_persists_summary_and_appointment_data(tmp_path):
    b = _salon(tmp_path)
    day = (TODAY + timedelta(days=5)).isoformat()
    appt = b.create_appointment(b.customers[0].id, b.items[0].id, "Elif", f"{day}T11:00")
    b.cancel_appointment(appt["id"])
    b.save()
    data = json.loads((tmp_path / "s.json").read_text())
    assert data["revenue_entries"] and data["leads"] and data["messages"]
    assert [c["change"] for c in data["appointment_changes"]] == ["created", "cancelled"]
    assert any(a["id"] == appt["id"] and a["status"] == "cancelled" for a in data["appointments"])
    assert any(c["created"] for c in data["customers"])


def test_no_phone_numbers_in_new_methods(tmp_path):
    b = _salon(tmp_path)
    day = (TODAY + timedelta(days=5)).isoformat()
    a = b.create_appointment(b.customers[0].id, b.items[0].id, "Elif", f"{day}T11:00")
    m = b.reschedule_appointment(a["id"], f"{day}T12:00")
    c = b.cancel_appointment(a["id"], reason="test")
    blob = json.dumps([b.daily_summary(day), b.daily_summary("2026-10-27"), b.find_appointments(),
                       b.find_appointments("ay"), a, m, c, b.report()])
    assert "phone" not in blob and "+90" not in blob and "5XX" not in blob


# --------------------------------------------------------------------------- #
# bulk price / loyalty / ads (task 07)
# --------------------------------------------------------------------------- #

def _preview(*rows):
    items = [{"id": f"x{i}", "name": f"n{i}", "old_price": old, "new_price": new}
             for i, (old, new) in enumerate(rows)]
    return {"pv1": {"preview_id": "pv1", "items": items, "count": len(items)}}


def _bulk_action(**kw):
    a = {"type": "apply_bulk_price", "preview_id": "pv1", "label": "2 kalem zam"}
    a.update(kw)
    return {"summary_tr": "Zam.", "actions": [a]}


def test_validate_bulk_price_accept_and_reject():
    pv = _preview((100, 110), (850, 765.0))
    assert validate_proposal(_bulk_action(), set(), CATALOG, set(), known_previews=pv) == []
    edge = _preview((100, 150), (100, 50))                       # exactly ±50% is allowed
    assert validate_proposal(_bulk_action(), set(), CATALOG, set(), known_previews=edge) == []

    errs = validate_proposal(_bulk_action(preview_id="pvX"), set(), CATALOG, set(), known_previews=pv)
    assert any("preview_id" in e for e in errs)
    errs = validate_proposal(_bulk_action(), set(), CATALOG, set(), known_previews=_preview())
    assert any("1..200" in e for e in errs)
    big = {"pv1": {"preview_id": "pv1",
                   "items": [{"id": "x", "old_price": 100.0, "new_price": 110.0}] * 201, "count": 201}}
    errs = validate_proposal(_bulk_action(), set(), CATALOG, set(), known_previews=big)
    assert any("1..200" in e for e in errs)
    errs = validate_proposal(_bulk_action(), set(), CATALOG, set(), known_previews=_preview((100, 0)))
    assert any("positive" in e for e in errs)
    errs = validate_proposal(_bulk_action(), set(), CATALOG, set(), known_previews=_preview((100, 151)))
    assert any("±50%" in e for e in errs)
    errs = validate_proposal(_bulk_action(), set(), CATALOG, set(), known_previews=_preview((100, 49)))
    assert any("±50%" in e for e in errs)
    prop = _bulk_action()
    del prop["actions"][0]["label"]
    errs = validate_proposal(prop, set(), CATALOG, set(), known_previews=pv)
    assert any("label" in e for e in errs)


def _loyalty_action(**kw):
    a = {"type": "adjust_loyalty", "customer_id": "c001", "points": 200, "reason": "doğum günü",
         "label": "Ayşe Y. +200 (doğum günü)"}
    a.update(kw)
    return {"summary_tr": "Puan.", "actions": [a]}


def test_validate_loyalty_accept_and_reject():
    assert validate_proposal(_loyalty_action(), set(), CATALOG, set(), known_customers={"c001"}) == []
    assert validate_proposal(_loyalty_action(points=-1000), set(), CATALOG, set(),
                             known_customers={"c001"}) == []
    prop = _loyalty_action(points="200")                          # numeric strings are coerced
    assert validate_proposal(prop, set(), CATALOG, set(), known_customers={"c001"}) == []
    assert prop["actions"][0]["points"] == 200

    errs = validate_proposal(_loyalty_action(), set(), CATALOG, set(), known_customers=set())
    assert any("customer_id" in e for e in errs)
    for bad in (0, 1001, -1001, 1.5, True):
        errs = validate_proposal(_loyalty_action(points=bad), set(), CATALOG, set(),
                                 known_customers={"c001"})
        assert any("points" in e for e in errs), bad
    prop = _loyalty_action()
    del prop["actions"][0]["reason"]
    errs = validate_proposal(prop, set(), CATALOG, set(), known_customers={"c001"})
    assert any("reason" in e for e in errs)
    prop = _loyalty_action()
    del prop["actions"][0]["label"]
    assert any("label" in e for e in validate_proposal(prop, set(), CATALOG, set(), known_customers={"c001"}))


def _ads_action(**kw):
    a = {"type": "ads_action", "campaign_id": "ad2", "action": "pause", "label": "Deri Çanta duraklat"}
    a.update(kw)
    return {"summary_tr": "Reklam.", "actions": [a]}


def test_validate_ads_action_accept_and_reject():
    known = {"ad1", "ad2"}
    assert validate_proposal(_ads_action(), set(), CATALOG, set(), known_campaigns=known) == []
    assert validate_proposal(_ads_action(action="resume"), set(), CATALOG, set(),
                             known_campaigns=known) == []
    assert validate_proposal(_ads_action(action="increase_budget", delta_pct=15), set(), CATALOG, set(),
                             known_campaigns=known) == []
    prop = _ads_action(action="decrease_budget", delta_pct="20")
    assert validate_proposal(prop, set(), CATALOG, set(), known_campaigns=known) == []
    assert prop["actions"][0]["delta_pct"] == 20

    errs = validate_proposal(_ads_action(), set(), CATALOG, set(), known_campaigns=set())
    assert any("campaign_id" in e for e in errs)
    errs = validate_proposal(_ads_action(action="boost"), set(), CATALOG, set(), known_campaigns=known)
    assert any("one of" in e for e in errs)
    errs = validate_proposal(_ads_action(action="increase_budget"), set(), CATALOG, set(),
                             known_campaigns=known)
    assert any("delta_pct" in e for e in errs)
    for bad in (0, 21, "x", True):
        errs = validate_proposal(_ads_action(action="decrease_budget", delta_pct=bad), set(), CATALOG,
                                 set(), known_campaigns=known)
        assert any("delta_pct" in e for e in errs), bad
    prop = _ads_action()
    del prop["actions"][0]["label"]
    assert any("label" in e for e in validate_proposal(prop, set(), CATALOG, set(), known_campaigns=known))


def test_preview_selection_and_math(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    pv = b.preview_bulk_price(category="elbise", mode="percent", value=10)
    assert pv["count"] == 1 and pv["items"][0]["name"] == "Midi Elbise"
    assert (pv["items"][0]["old_price"], pv["items"][0]["new_price"]) == (1290.0, 1419.0)
    assert pv["preview_id"] in b.previews
    assert b.items[2].price == 1290.0                              # preview changes nothing

    all_products = b.preview_bulk_price(item_type="product", mode="amount", value=-50)
    assert all_products["count"] == 3
    assert {r["new_price"] for r in all_products["items"]} == {600.0, 370.0, 1240.0}
    one = b.preview_bulk_price(item_ids=["i001", "i003"], mode="percent", value=0)
    assert [r["id"] for r in one["items"]] == ["i001", "i003"]
    assert b.preview_bulk_price(category="yok-böyle-kategori", mode="percent", value=10)["count"] == 0
    with pytest.raises(ValueError, match="unknown catalog item"):
        b.preview_bulk_price(item_ids=["ghost"], mode="percent", value=10)
    with pytest.raises(ValueError, match="mode"):
        b.preview_bulk_price(mode="double", value=10)
    with pytest.raises(ValueError, match="sayı"):
        b.preview_bulk_price(mode="percent", value="çok")
    with pytest.raises(ValueError, match="item_type"):
        b.preview_bulk_price(item_type="pizza", mode="percent", value=10)
    blob = json.dumps([pv, all_products, one])
    assert "phone" not in blob and "5XX" not in blob


def test_ads_performance_and_suggestions(tmp_path):
    for business in ("butik", "salon"):
        b = MockBackend(business, today=TODAY, store_path=tmp_path / f"{business}.json")
        perf = b.ads_performance()
        assert [c["id"] for c in perf["campaigns"]] == ["ad1", "ad2", "ad3"]
        assert set(perf["campaigns"][0]) == {"id", "name", "platform", "status", "daily_budget",
                                             "spend", "impressions", "clicks", "results",
                                             "cost_per_result"}
        assert perf["totals"]["results"] == sum(c["results"] for c in perf["campaigns"])
        assert perf["totals"]["spend"] == round(sum(c["spend"] for c in perf["campaigns"]), 2)
        by_id = {c["id"]: c for c in perf["campaigns"]}
        assert by_id["ad2"]["cost_per_result"] > by_id["ad1"]["cost_per_result"] * 3  # clear loser
        fort = b.ads_performance(14)
        assert fort["campaigns"][0]["spend"] == perf["campaigns"][0]["spend"] * 2
        sug = b.ads_suggestions()["suggestions"]
        assert 1 <= len(sug) <= 2 and all(set(s) == {"campaign_id", "kind", "text"} for s in sug)
        assert "5XX" not in json.dumps([perf, sug])
    with pytest.raises(ValueError, match="days"):
        MockBackend("butik", today=TODAY, store_path=tmp_path / "x.json").ads_performance(0)


def test_loyalty_points_seeded(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    pts = [c.loyalty_points for c in b.customers]
    assert all(isinstance(p, int) and p >= 0 for p in pts) and any(p > 0 for p in pts)


def test_apply_bulk_price_semantics_and_expiry(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    pv = b.preview_bulk_price(category="aksesuar", mode="amount", value=80)
    res = b.apply_bulk_price(pv["preview_id"])
    assert res["count"] == 1 and res["items"][0]["old_price"] == 420.0
    assert next(i for i in b.items if i.id == "i002").price == 500.0
    with pytest.raises(ValueError, match="bulunamadı"):
        b.apply_bulk_price("pv-ghost")
    stale = b.preview_bulk_price(mode="percent", value=5)
    b.previews[stale["preview_id"]]["at"] -= 16 * 60
    with pytest.raises(ValueError, match="süresi doldu"):
        b.apply_bulk_price(stale["preview_id"])


def test_adjust_loyalty_semantics(tmp_path):
    b = _salon(tmp_path)
    c = b.customers[0]
    res = b.adjust_loyalty(c.id, 200, "doğum günü")
    assert (res["old_points"], res["points"], res["delta"]) == (c.loyalty_points - 200, c.loyalty_points, 200)
    res = b.adjust_loyalty(c.id, -100_000, "düzeltme")
    assert res["points"] == 0                                       # KEDD clamps below zero
    with pytest.raises(ValueError, match="Müşteri bulunamadı"):
        b.adjust_loyalty("cx", 10, "x")


def test_ads_action_semantics(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    assert b.ads_action("ad1", "pause")["status"] == "paused"
    assert b.ads_action("ad1", "pause")["status"] == "paused"      # idempotent
    assert b.ads_action("ad1", "resume")["status"] == "active"
    assert b.ads_action("ad1", "increase_budget", 10)["daily_budget"] == 550.0
    assert b.ads_action("ad1", "decrease_budget", 20)["daily_budget"] == 440.0
    with pytest.raises(ValueError, match="Duraklatılmış"):
        b.ads_action("ad3", "increase_budget", 10)                 # ad3 starts paused
    with pytest.raises(ValueError, match="Reklam bulunamadı"):
        b.ads_action("adX", "pause")
    with pytest.raises(ValueError, match="anlaşılmadı"):
        b.ads_action("ad1", "boost")
    with pytest.raises(ValueError, match="delta_pct"):
        b.ads_action("ad1", "increase_budget")


def test_executor_new_actions_and_card(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    pv = b.preview_bulk_price(category="elbise", mode="percent", value=10)
    cust = b.customers[0]
    prop = {"summary_tr": "Zam, puan ve reklam.", "actions": [
        {"type": "apply_bulk_price", "preview_id": pv["preview_id"],
         "label": "1 kalem (Midi Elbise 1290→1419 ₺)"},
        {"type": "adjust_loyalty", "customer_id": cust.id, "points": 200, "reason": "doğum günü",
         "label": f"{cust.name} +200 (doğum günü)"},
        {"type": "ads_action", "campaign_id": "ad2", "action": "pause",
         "label": "Deri Çanta duraklat"},
    ]}
    ctx = MediaContext(instruction="x", instruction_source="text")
    steps = executor.execute(prop, ctx, b, out_dir=tmp_path)
    assert [s["status"] for s in steps] == ["ok", "ok", "ok"]
    assert steps[0]["result"]["updated"] == 1 and b.items[2].price == 1419.0
    assert cust.loyalty_points >= 200 and steps[1]["result"]["delta"] == 200
    assert next(c for c in b.ads if c.id == "ad2").status == "paused"
    # The card is rendered from tool facts, not from the model's label: a lying label changes nothing.
    perf = b.ads_performance(7)
    log = [{"tool": "preview_bulk_price", "result": pv},
           {"tool": "find_customer", "result": {"matches": [{"id": cust.id, "name": cust.name}]}},
           {"tool": "get_ads_performance", "result": perf}]
    for a in prop["actions"]:
        a["label"] = "her şey %1 indirimli, kimse etkilenmez"
    card = approval.render_card(prop, ctx, {}, approval.card_facts(log))
    assert "💸 Toplu fiyat: 1 kalem — Midi Elbise 1,290→1,419 ₺" in card
    assert f"⭐ Puan: {cust.name} +200 (doğum günü)" in card
    ad2 = next(c for c in perf["campaigns"] if c["id"] == "ad2")
    assert f"📢 Reklam: {ad2['name']} — durdurulacak" in card
    assert "kimse etkilenmez" not in card
    # without facts the card refuses to describe an unknown preview
    assert "önizleme bulunamadı" in approval.render_card(prop, ctx, {})


class _FakeSession:
    """Captures KEDD adapter traffic without any network."""

    def __init__(self, payload=None, status=200):
        self.calls: list[tuple] = []
        self.payload = {"ok": True} if payload is None else payload
        self.status = status

    def _resp(self):
        return SimpleNamespace(json=lambda: self.payload, status_code=self.status,
                               raise_for_status=lambda: None)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers, None))
        return self._resp()

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, headers, json.loads(data)))
        return self._resp()


def test_kedd_paths_and_bodies():
    kb = KeddBackend("https://kedd.example", "s3cret", "905321")
    kb._s = _FakeSession()
    kb.preview_bulk_price(category="çanta", mode="percent", value=10)
    kb.ads_performance(30)
    kb.ads_suggestions()
    kb.apply_bulk_price("pv101")
    calls = kb._s.calls
    assert [(m, u) for m, u, _h, _b in calls] == [
        ("POST", "https://kedd.example/agent/catalog/bulk-price/preview"),
        ("GET", "https://kedd.example/agent/ads/performance?days=30"),
        ("GET", "https://kedd.example/agent/ads/suggestions"),
        ("POST", "https://kedd.example/agent/catalog/bulk-price"),
    ]
    assert calls[0][3] == {"percent": 10, "category": "çanta"}
    assert calls[3][3] == {"preview_id": "pv101"}
    for _m, _u, headers, _b in calls:
        assert {"X-Agent-Phone", "X-Agent-Timestamp", "X-Agent-Signature"} <= set(headers)
        assert headers["X-Agent-Phone"] == "905321"
    with pytest.raises(ValueError, match="henüz bağlı değil"):
        kb.adjust_loyalty("c001", 200, "doğum günü")
    with pytest.raises(ValueError, match="henüz bağlı değil"):
        kb.ads_action("ad2", "increase_budget", 15)
    assert len(kb._s.calls) == 4  # unimplemented writes never touch HTTP


# --------------------------------------------------------------------------- #
# template campaigns (task 08)
# --------------------------------------------------------------------------- #

def _known_templates():
    return {t["name"]: t for t in MockBackend("butik", today=TODAY).campaign_templates()}


def _template_proposal(**kw):
    a = {"type": "send_campaign", "segment_id": "seg1",
         "template": "marketing_reengagement_promo_tr",
         "variables": {"2": "FULAR15", "3": "hafta sonu tüm fularlarda %15 indirim"}}
    a.update(kw)
    return {"summary_tr": "Fularlarda %15, gelmeyenlere mesaj.", "actions": [
        {"type": "create_discount", "percent": 15, "item_refs": ["i001"],
         "starts": "2026-10-31", "ends": "2026-11-01"},
        a,
    ]}


def test_campaign_templates_shape():
    templates = MockBackend("butik", today=TODAY).campaign_templates()
    assert [t["name"] for t in templates] == ["marketing_reengagement_promo_tr",
                                             "marketing_engagement_thankyou_promo_tr",
                                             "marketing_welcome_offer_friendly_tr"]
    assert all(set(t) == {"name", "body", "variables"} for t in templates)
    by_name = {t["name"]: t for t in templates}
    assert [(v["number"], v["name"]) for v in by_name["marketing_reengagement_promo_tr"]["variables"]] == [
        (1, "name"), (2, "code"), (3, "advantage")]
    assert [(v["number"], v["name"]) for v in by_name["marketing_engagement_thankyou_promo_tr"]
                ["variables"]] == [(1, "name"), (2, "code"), (3, "discount")]
    assert [(v["number"], v["name"]) for v in by_name["marketing_welcome_offer_friendly_tr"]
                ["variables"]] == [(1, "name")]
    for t in templates:
        assert "{{1}}" in t["body"] and len(t["body"]) <= 350
        assert "%" not in t["body"] and "TL" not in t["body"] and "₺" not in t["body"]
    templates[0]["body"] = "mutated"          # callers get copies, not the registry
    assert MockBackend("butik", today=TODAY).campaign_templates()[0]["body"] != "mutated"


def test_validate_template_campaign_ok():
    assert validate_proposal(_template_proposal(), set(), CATALOG, {"seg1"},
                             known_templates=_known_templates()) == []
    welcome = {"summary_tr": "Yeni üyeye hoş geldin mesajı.", "actions": [
        {"type": "send_campaign", "segment_id": "seg1",
         "template": "marketing_welcome_offer_friendly_tr"}]}
    assert validate_proposal(welcome, set(), CATALOG, {"seg1"}, known_templates=_known_templates()) == []
    welcome["actions"][0]["variables"] = {}   # explicitly empty is also fine (only {{1}} exists)
    assert validate_proposal(welcome, set(), CATALOG, {"seg1"}, known_templates=_known_templates()) == []


def test_validate_template_must_come_from_tool():
    errs = validate_proposal(_template_proposal(template="marketing_uydurma"), set(), CATALOG, {"seg1"},
                             known_templates=_known_templates())
    assert any("list_campaign_templates" in e for e in errs)
    errs = validate_proposal(_template_proposal(), set(), CATALOG, {"seg1"}, known_templates={})
    assert any("list_campaign_templates" in e for e in errs)


def test_validate_template_variables_exact():
    base = _known_templates()
    prop = _template_proposal(variables={"2": "FULAR15"})
    assert any("needs variables ['3']" in e for e in validate_proposal(prop, set(), CATALOG, {"seg1"},
                                                                       known_templates=base))
    prop = _template_proposal(variables={"2": "FULAR15", "3": "x", "4": "y"})
    assert any("has no variables ['4']" in e for e in validate_proposal(prop, set(), CATALOG, {"seg1"},
                                                                        known_templates=base))
    prop = _template_proposal(variables={"1": "Ayşe", "2": "FULAR15", "3": "x"})
    assert any("must not include '1'" in e for e in validate_proposal(prop, set(), CATALOG, {"seg1"},
                                                                      known_templates=base))
    prop = _template_proposal(variables=["FULAR15"])
    assert any("must be an object" in e for e in validate_proposal(prop, set(), CATALOG, {"seg1"},
                                                                   known_templates=base))
    prop = _template_proposal(template="marketing_welcome_offer_friendly_tr", variables={"2": "x"})
    assert any("has no variables ['2']" in e for e in validate_proposal(prop, set(), CATALOG, {"seg1"},
                                                                        known_templates=base))


def test_validate_template_variable_values():
    base = _known_templates()

    def errs_for(value):
        prop = _template_proposal(variables={"2": "FULAR15", "3": value})
        return validate_proposal(prop, set(), CATALOG, {"seg1"}, known_templates=base)

    assert any("> 60" in e for e in errs_for("x" * 61))
    assert any("newline" in e for e in errs_for("satır bir\nsatır iki"))
    assert any("URL" in e for e in errs_for("bak https://ornek.com/firsat"))
    assert any("URL" in e for e in errs_for("bak www.ornek.com"))
    assert any("must be a string" in e for e in errs_for(15))
    assert errs_for("x" * 60) == []                       # exactly 60 is allowed


def test_validate_template_rendered_numbers_checked():
    base = _known_templates()
    prop = _template_proposal(variables={"2": "FULAR15", "3": "hafta sonu %30 indirim"})
    assert any("%30" in e for e in validate_proposal(prop, set(), CATALOG, {"seg1"},
                                                     known_templates=base))
    prop = _template_proposal(variables={"2": "FULAR15", "3": "fular 999 TL"})
    assert any("999" in e for e in validate_proposal(prop, set(), CATALOG, {"seg1"},
                                                     known_templates=base))
    prop = _template_proposal(variables={"2": "FULAR15", "3": "yüzde 30 indirim"})
    assert any("%30" in e for e in validate_proposal(prop, set(), CATALOG, {"seg1"},
                                                     known_templates=base))


def test_validate_message_and_template_exclusive():
    prop = _template_proposal(message="Selam!")
    assert any("must be omitted" in e for e in validate_proposal(prop, set(), CATALOG, {"seg1"},
                                                                 known_templates=_known_templates()))


def test_validate_kedd_requires_template():
    object.__setattr__(settings, "backend", "kedd")
    try:
        assert validate_proposal(_template_proposal(), set(), CATALOG, {"seg1"},
                                 known_templates=_known_templates()) == []
        errs = validate_proposal(_proposal("seg101"), {"tmp1"}, CATALOG, {"seg101"})
        assert any("template is required" in e for e in errs)
    finally:
        object.__setattr__(settings, "backend", "mock")
    assert validate_proposal(_proposal("seg101"), {"tmp1"}, CATALOG, {"seg101"}) == []  # mock free-text ok


def test_template_card_renders_from_facts(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    seg = b.find_customers(inactive_days=60)
    log = [{"tool": "list_campaign_templates", "result": {"templates": b.campaign_templates()}}]
    facts = approval.card_facts(log)
    prop = {"summary_tr": "x", "actions": [
        {"type": "send_campaign", "segment_id": seg["segment_id"],
         "template": "marketing_reengagement_promo_tr",
         "variables": {"2": "FULAR15", "3": "hafta sonu %15 indirim"},
         "message": "YALAN MESAJ %99 bedava"},
    ]}
    card = approval.render_card(prop, MediaContext("x", "text"), {seg["segment_id"]: seg["count"]}, facts)
    assert "‹müşteri adı›" in card and "FULAR15" in card and "hafta sonu %15 indirim" in card
    assert "YALAN MESAJ" not in card and "%99" not in card
    no_facts = approval.render_card(prop, MediaContext("x", "text"), {})
    assert "şablon bulunamadı" in no_facts  # no facts → no invented body


def test_executor_template_campaign_mock(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    seg = b.find_customers(inactive_days=60)
    prop = {"summary_tr": "x", "actions": [
        {"type": "send_campaign", "segment_id": seg["segment_id"],
         "template": "marketing_reengagement_promo_tr",
         "variables": {"2": "FULAR15", "3": "hafta sonu %15 indirim"}}]}
    steps = executor.execute(prop, MediaContext("x", "text"), b, out_dir=tmp_path)
    assert steps[0]["status"] == "ok" and steps[0]["result"]["recipients"] == seg["count"]
    stored = b.campaigns[0]
    assert stored.template == "marketing_reengagement_promo_tr"
    assert stored.variables == {"2": "FULAR15", "3": "hafta sonu %15 indirim"}
    first_name = next(c.name for c in b.customers if c.id == b.segments[seg["segment_id"]][0])
    assert first_name in stored.message and "FULAR15" in stored.message and "{{" not in stored.message
    prop["actions"][0]["template"] = "ghost"              # unknown template fails closed
    assert executor.execute(prop, MediaContext("x", "text"), b, out_dir=tmp_path)[0]["status"] == "failed"
    assert len(b.campaigns) == 1
    free = b.send_campaign("k2", seg["segment_id"], "merhaba", "")   # free-text signature unchanged
    assert free["template"] == "" and free["message"] == "merhaba"


def test_kedd_template_paths_and_bodies():
    kb = KeddBackend("https://kedd.example", "s3cret", "905321")
    kb._s = _FakeSession()
    kb.campaign_templates()
    calls = kb._s.calls
    assert calls[0][:2] == ("GET", "https://kedd.example/agent/campaign-templates")
    with pytest.raises(ValueError, match="henüz bağlı değil"):
        kb.send_campaign("Kış", "seg9", template="marketing_reengagement_promo_tr",
                         variables={"2": "FULAR15", "3": "hafta sonu %15"})
    with pytest.raises(ValueError, match="henüz bağlı değil"):
        kb.send_campaign("Eski", "seg9", "merhaba", "")
    assert len(calls) == 1  # the gateway has no campaign endpoint yet (K4)


def test_list_campaign_templates_tool_and_prompt(tmp_path):
    b = MockBackend("butik", today=TODAY, store_path=tmp_path / "s.json")
    run = agent.AgentRun()
    res = agent._run_tool("list_campaign_templates", {}, b, run)
    assert {t["name"] for t in res["templates"]} == set(run.templates) and len(run.templates) == 3
    assert "list_campaign_templates" in {t["name"] for t in agent.TOOLS}
    assert "list_campaign_templates" in agent.SYSTEM_PROMPT and "FULAR15" in agent.SYSTEM_PROMPT
    assert "{{1}}" in agent.SYSTEM_PROMPT


def test_appointment_card_lines_come_from_tool_facts():
    log = [{"tool": "find_customer", "result": {"matches": [{"id": "c001", "name": "Ayşe Y."}]}},
           {"tool": "list_catalog", "result": {"items": [{"id": "i001", "name": "Saç Kesimi"}]}},
           {"tool": "find_appointments", "result": {"appointments": [
               {"id": "a007", "customer": "Ayşe A.", "service": "Saç Boyama", "starts": "2026-10-29T10:00"}]}}]
    prop = {"summary_tr": "x", "actions": [
        {"type": "create_appointment", "customer_id": "c001", "item_ref": "i001", "staff": "Elif",
         "starts": "2026-10-31T15:00", "label": "YANLIŞ ETİKET"},
        {"type": "reschedule_appointment", "appointment_id": "a007", "starts": "2026-10-29T11:00", "label": "YANLIŞ"},
        {"type": "cancel_appointment", "appointment_id": "a007", "label": "YANLIŞ"}]}
    card = approval.render_card(prop, MediaContext("x", "text"), {}, approval.card_facts(log))
    assert "📅 Randevu: Ayşe Y. — Saç Kesimi, Elif, 31.10 15:00" in card
    assert "🔁 Randevu taşınıyor: Ayşe A. — Saç Boyama, 29.10 10:00 → 29.10 11:00" in card
    assert "❌ Randevu iptal: Ayşe A. — Saç Boyama, 29.10 10:00" in card
    assert "YANLIŞ" not in card


# --------------------------------------------------------------------------- #
# KEDD mode per conversation (task 09)
# --------------------------------------------------------------------------- #

def test_kedd_backend_pickles_without_session_and_hides_secret():
    kb = KeddBackend("https://kedd.example", "s3cret", "+905321")
    kb2 = pickle.loads(pickle.dumps(kb))
    assert kb2.base == "https://kedd.example" and kb2._phone == "+905321"
    assert kb2._schema == KeddBackend.SCHEMA
    assert "s3cret" not in repr(kb2) and "5321" in repr(kb2)
    h = kb2._headers("GET", "/agent/profile", "")        # signing works after the round trip
    assert h["X-Agent-Phone"] == "+905321" and len(h["X-Agent-Signature"]) == 64


def test_kedd_gateway_error_becomes_turkish_valueerror():
    kb = KeddBackend("https://kedd.example", "s3cret", "+905321")
    kb._s = _FakeSession(status=400, payload={"error": "bad_request", "message": "Gün anlaşılmadı."})
    with pytest.raises(ValueError) as e:
        kb.get_schedule("zzz")
    assert str(e.value) == "Gün anlaşılmadı."            # the Turkish message, no HTTPError text
    kb._s = _FakeSession(status=500, payload={"error": "boom"})
    with pytest.raises(ValueError, match=r"boom.*HTTP 500"):
        kb.store_profile()
    kb._s = _FakeSession(status=503, payload="<html>kapalı</html>")
    with pytest.raises(ValueError, match="HTTP 503"):
        kb.store_profile()


@pytest.mark.parametrize("code", ["in_progress", "unknown_outcome"])
def test_kedd_409_says_may_have_run_and_not_retried(code):
    kb = KeddBackend("https://kedd.example", "s3cret", "+905321")
    kb._s = _FakeSession(status=409, payload={"error": code, "message": "İşlemin sonucu belirsiz."})
    with pytest.raises(ValueError) as e:
        kb.apply_bulk_price("pv101")
    assert "tekrar denemedim" in str(e.value) and "olabilir" in str(e.value)
    assert "İşlemin sonucu belirsiz." in str(e.value)    # the gateway message is kept


def test_kedd_post_defaults_to_fresh_idempotency_key():
    kb = KeddBackend("https://kedd.example", "s3cret", "+905321")
    kb._s = _FakeSession()
    kb.apply_bulk_price("pv1")
    kb.apply_bulk_price("pv1", idempotency_key="run-7")
    kb.find_customers(inactive_days=60)                  # reads-over-POST carry one too
    keys = [h["Idempotency-Key"] for _m, _u, h, _b in kb._s.calls]
    assert keys[0] and keys[1] == "run-7" and keys[2] and len(set(keys)) == 3


def test_executor_passes_stable_idempotency_key(tmp_path):
    kb = KeddBackend("https://kedd.example", "s3cret", "+905321")
    kb._s = _FakeSession(payload={"id": "k1", "type": "product", "name": "Fular", "price": 850,
                                  "percent": 15})
    prop = {"summary_tr": "x", "actions": [
        {"type": "add_catalog_item", "item_type": "product", "ref": "n1", "name": "Fular", "price": 850},
        {"type": "create_discount", "percent": 15, "item_refs": ["n1"],
         "starts": "2026-10-31", "ends": "2026-11-01"}]}
    run_dir = tmp_path / "20261028-120000-abcdef"
    run_dir.mkdir()
    steps = executor.execute(prop, MediaContext("x", "text"), kb, out_dir=run_dir)
    assert [s["status"] for s in steps] == ["ok", "ok"]
    keys = [h["Idempotency-Key"] for _m, _u, h, _b in kb._s.calls]
    assert keys == ["20261028-120000-abcdef-0", "20261028-120000-abcdef-1"]
    executor.execute(prop, MediaContext("x", "text"), kb, out_dir=run_dir)   # a retried delivery…
    keys2 = [h["Idempotency-Key"] for _m, _u, h, _b in kb._s.calls[2:]]
    assert keys2 == keys                                                     # …reuses both keys
