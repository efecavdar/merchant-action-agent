"""Offline tests: everything that does not need an API key."""

from datetime import date

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
