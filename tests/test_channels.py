"""WhatsApp channel + conversation core + webhook, all offline."""

import hashlib
import hmac
import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import agent, core, report, server
from app.channels import whatsapp as wa
from app.config import settings
from app.tools.backend import MockBackend

OURS = "897760253429206"
SECRET = "s3cret"


@pytest.fixture
def cfg():
    """Settings is a frozen dataclass; patch fields for one test and restore them."""
    saved = {}

    def _set(name, value):
        saved.setdefault(name, getattr(settings, name))
        object.__setattr__(settings, name, value)
    yield _set
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def _payload(messages, pnid=OURS, field="messages"):
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{
        "field": field, "value": {"messaging_product": "whatsapp",
                                  "metadata": {"display_phone_number": "1", "phone_number_id": pnid},
                                  "messages": messages}}]}]}


def test_parse_webhook_text_audio_image_and_filters():
    msgs = [
        {"id": "m1", "from": "905321", "timestamp": "1", "type": "text", "text": {"body": "evet"}},
        {"id": "m2", "from": "905321", "timestamp": "2", "type": "audio",
         "audio": {"id": "med1", "mime_type": "audio/ogg; codecs=opus", "voice": True}},
        {"id": "m3", "from": "905321", "timestamp": "3", "type": "image",
         "image": {"id": "med2", "mime_type": "image/jpeg", "caption": "bunları ekle"}},
        {"id": "m4", "from": "905321", "timestamp": "4", "type": "sticker", "sticker": {"id": "x"}},
    ]
    out = wa.parse_webhook(_payload(msgs), OURS)
    assert [(m.kind, m.text, m.media_id) for m in out] == [
        ("text", "evet", ""), ("audio", "", "med1"), ("image", "bunları ekle", "med2"), ("other", "", "")]
    assert wa.parse_webhook(_payload(msgs, pnid="other"), OURS) == []          # not our number
    assert wa.parse_webhook(_payload(msgs, field="statuses"), OURS) == []      # delivery receipts
    assert wa.parse_webhook({"object": "page"}, OURS) == []
    assert wa.is_allowed("+905321", frozenset({"905321"})) and not wa.is_allowed("905322", frozenset({"905321"}))


def test_signature():
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert wa.verify_signature(body, good, SECRET)
    assert not wa.verify_signature(body + b" ", good, SECRET)
    assert not wa.verify_signature(body, None, SECRET)
    assert not wa.verify_signature(body, good, "")                            # no secret configured → reject


# --------------------------------------------------------------------------- #
# core
# --------------------------------------------------------------------------- #

def _fake_plan(ctx, backend, max_revisions=2):
    seg = backend.find_customers(inactive_days=60)
    run = SimpleNamespace(turns=1, usage={"input_tokens": 1, "output_tokens": 1}, segments={seg["segment_id"]},
                          tool_log=[{"tool": "find_customers", "args": {}, "result": seg}])
    run.proposal = {"summary_tr": "Fular ekleniyor, 1 kampanya.", "actions": [
        {"type": "add_catalog_item", "item_type": "product", "ref": "n1", "name": "İpek Fular", "price": 850},
        {"type": "send_campaign", "segment_id": seg["segment_id"], "message": "Fular 850 TL geldi!"}]}
    return run


def test_core_flow_plan_approve_execute(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep: "Hallettim.")
    s = core.Session(sender="905321", backend=MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s"))
    notes = []

    assert "Çırak" in core.handle(s, "text", "merhaba")[0].text
    r = core.handle(s, "text", "Fular ekle 850 lira, gelmeyenlere haber ver", notify=notes.append)
    assert s.pending and "onayına sunuyorum" in r[-1].text and "850" in r[-1].text and notes == ["⏳ Bakıyorum…"]
    assert s.backend.campaigns == []                                           # nothing ran yet

    r = core.handle(s, "text", "Evet!")
    assert s.pending is None and r[0].text.startswith("Hallettim")
    assert len(s.backend.campaigns) == 1 and s.backend.items[-1].name == "İpek Fular"
    assert core.handle(s, "text", "evet")[0].text != "Hallettim."              # second "evet" cannot re-run


def test_core_reject_and_photo_buffer(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    s = core.Session(sender="905321", backend=MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s"))
    assert "1 fotoğraf aldım" in core.handle(s, "image", media_path="a.jpg")[0].text
    assert core.handle(s, "image", media_path="b.jpg") == [] and s.images == ["a.jpg", "b.jpg"]
    seen = {}
    monkeypatch.setattr(core.pipeline, "understand_media",
                        lambda text, audio, images, concept=None, out_dir=None: seen.update(images=images) or
                        core.pipeline.MediaContext(instruction=text, instruction_source="text"))
    core.handle(s, "text", "bunları ekle 850")
    assert seen["images"] == ["a.jpg", "b.jpg"] and s.images == [] and s.pending
    assert "iptal" in core.handle(s, "text", "hayır")[0].text and s.pending is None and s.backend.campaigns == []
    assert "Studio Elif" in core.handle(s, "text", "/salon")[0].text and s.business == "salon"


# --------------------------------------------------------------------------- #
# webhook
# --------------------------------------------------------------------------- #

def test_webhook_verify_and_dispatch(cfg):
    cfg("wa_verify_token", "vt")
    cfg("meta_app_secret", SECRET)
    cfg("wa_phone_number_id", OURS)
    cfg("wa_allowed_senders", frozenset({"905321"}))
    got, seen = [], set()
    c = TestClient(server.create_app(got.append, lambda mid: not (mid in seen or seen.add(mid))))

    r = c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"
    assert c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "42"}).status_code == 403

    body = json.dumps(_payload([
        {"id": "m1", "from": "905321", "timestamp": "1", "type": "text", "text": {"body": "selam"}},
        {"id": "m9", "from": "905999", "timestamp": "1", "type": "text", "text": {"body": "hack"}},
    ])).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert c.post("/webhook", content=body, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 401
    assert c.post("/webhook", content=body, headers={"x-hub-signature-256": sig}).status_code == 200
    assert c.post("/webhook", content=body, headers={"x-hub-signature-256": sig}).status_code == 200  # redelivery
    assert [m["id"] for m in got] == ["m1"]       # stranger dropped, duplicate dropped, once
