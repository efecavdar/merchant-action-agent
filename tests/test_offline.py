"""Offline tests: everything that does not need an API key."""

import json
import unicodedata
from datetime import date, timedelta

import pytest
from PIL import Image

from app.actions import validate_proposal
from app.meta_client import extract_json, parse_sam_boxes
from app.pipeline import MediaContext, ProductCard
from app.tools.backend import MockBackend
from app import approval, executor


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
