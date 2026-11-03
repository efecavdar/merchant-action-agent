"""Gateway K17: the owner's photo reaches KEDD, and an existing item is updated, never added twice."""

import base64
import json
from datetime import date
from types import SimpleNamespace

from PIL import Image

from app import actions, approval, executor, i18n
from app.pipeline import MediaContext, ProductCard
from app.tools.backend import KeddBackend, MockBackend

BASE = "https://kedd.example"
CATALOG = {"it-1": {"id": "it-1", "type": "service", "name": "Web Site Bakımı", "price": 2000}}


class _Stub:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url[len(BASE):], json.loads(data)))
        return SimpleNamespace(json=lambda: self.routes[url[len(BASE):]], status_code=200)


def _kb(routes, key_id="main"):
    kb = KeddBackend(BASE, "s3cret", "+905321", key_id=key_id)
    kb._s = _Stub(routes)
    return kb


def _photo(tmp_path):
    p = tmp_path / "crop.png"
    Image.new("RGB", (16, 16), "red").save(p)
    return str(p)


def _ctx(tmp_path):
    card = ProductCard("tmp1", _photo(tmp_path), "src.jpg", "Görsel", "diğer", "", "", "")
    return MediaContext(instruction="x", instruction_source="text", products=[card])


def _validate(acts):
    return actions.validate_proposal({"summary_tr": "x", "actions": acts}, {"tmp1"}, CATALOG, set())


def test_adding_an_item_that_already_exists_is_rejected_with_a_pointer_to_update():
    errs = _validate([{"type": "add_catalog_item", "item_type": "service", "temp_id": "tmp1",
                       "name": "web site  BAKIMI", "price": 2000}])
    assert any("already exists (id it-1)" in e and "update_catalog_item" in e for e in errs)


def test_update_needs_a_known_item_and_a_photo_or_name_and_cannot_touch_price():
    assert _validate([{"type": "update_catalog_item", "item_id": "it-1", "temp_id": "tmp1"}]) == []
    assert _validate([{"type": "update_catalog_item", "item_id": "it-1", "name": "Bakım Paketi"}]) == []
    assert any("item_id" in e for e in _validate([{"type": "update_catalog_item", "item_id": "x",
                                                    "temp_id": "tmp1"}]))
    assert any("temp_id" in e for e in _validate([{"type": "update_catalog_item", "item_id": "it-1"}]))
    assert any("price cannot be changed" in e
               for e in _validate([{"type": "update_catalog_item", "item_id": "it-1", "temp_id": "tmp1",
                                    "price": 1}]))


def test_update_after_inbox_read_is_quarantined():
    errs = actions.validate_proposal({"summary_tr": "x", "actions": [
        {"type": "update_catalog_item", "item_id": "it-1", "name": "X"}]}, {"tmp1"}, CATALOG, set(),
        inbox_read=True)
    assert any("not allowed after an inbox read" in e for e in errs)


def test_kedd_add_sends_photo_and_duration_under_gateway_names(tmp_path):
    kb = _kb({"/agent/catalog": {"id": "new-1", "type": "service", "name": "Bakım", "price": 2000}})
    res = kb.add_catalog_item("service", "Bakım", 2000, "diğer", "d", image_path=_photo(tmp_path),
                              duration_minutes=120, idempotency_key="k1")
    body = kb._s.calls[0][1]
    assert body["durationMinutes"] == 120 and "duration_minutes" not in body
    assert body["image_mime"] == "image/jpeg" and base64.b64decode(body["image_base64"])[:3] == b"\xff\xd8\xff"
    assert res["image_path"]


def test_kedd_update_posts_photo_only(tmp_path):
    kb = _kb({"/agent/catalog/update": {"id": "it-1", "name": "Web Site Bakımı", "price": 2000}})
    res = kb.update_catalog_item("it-1", image_path=_photo(tmp_path), idempotency_key="k2")
    path, body = kb._s.calls[0]
    assert path == "/agent/catalog/update" and body["item_id"] == "it-1" and "name" not in body
    assert body["image_base64"] and res["name"] == "Web Site Bakımı" and res["image_path"]


def test_demo_key_never_uploads_photos(tmp_path):
    kb = _kb({"/agent/catalog": {"id": "n", "type": "product", "name": "A", "price": 5}}, key_id="demo")
    kb.add_catalog_item("product", "A", 5, "", "", image_path=_photo(tmp_path))
    assert "image_base64" not in kb._s.calls[0][1]


def test_execute_update_sets_the_photo_and_reports_it(tmp_path):
    mb = MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s")
    item = mb.list_catalog()[0]
    ctx = _ctx(tmp_path)
    steps = executor.execute({"summary_tr": "x", "actions": [
        {"type": "update_catalog_item", "item_id": item["id"], "temp_id": "tmp1"}]}, ctx, mb, tmp_path)
    assert steps[0]["status"] == "ok" and steps[0]["result"]["photo_saved"]
    assert mb.list_catalog()[0]["image_path"] == ctx.products[0].crop_path
    line = i18n.render_progress("ok", 1, 1, steps[0], "tr")
    assert line == f"✅ 1/1 Güncellendi: {item['name']} — fotoğraf eklendi"


def test_card_names_the_existing_item_and_the_change(tmp_path):
    card = approval.render_card(
        {"summary_tr": "özet", "actions": [{"type": "update_catalog_item", "item_id": "it-1", "temp_id": "tmp1"}]},
        _ctx(tmp_path), {}, approval.card_facts([{"tool": "list_catalog",
                                                  "result": {"items": list(CATALOG.values())}}]))
    assert "✏️ Güncelle: Web Site Bakımı — fotoğrafı eklenecek 📷" in card
