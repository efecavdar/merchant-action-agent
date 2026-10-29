"""WhatsApp channel + conversation core + webhook, all offline."""

import hashlib
import hmac
import json
import pickle
import time
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import agent, core, report, server
from app.channels import whatsapp as wa
from app.config import settings
from app.tools.backend import KeddBackend, MockBackend

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

def _fake_plan(ctx, backend, max_revisions=2, lang="tr"):
    seg = backend.find_customers(inactive_days=60)
    run = SimpleNamespace(turns=1, usage={"input_tokens": 1, "output_tokens": 1}, segments={seg["segment_id"]},
                          answer=None, tool_log=[{"tool": "find_customers", "args": {}, "result": seg}])
    run.proposal = {"summary_tr": "Fular ekleniyor, 1 kampanya.", "actions": [
        {"type": "add_catalog_item", "item_type": "product", "ref": "n1", "name": "İpek Fular", "price": 850},
        {"type": "send_campaign", "segment_id": seg["segment_id"], "message": "Fular 850 TL geldi!"}]}
    return run


def test_core_flow_plan_approve_execute(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep, lang="tr": "Hallettim.")
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


def test_core_stale_backend_is_reseeded_and_report_failure_is_honest(monkeypatch, cfg, tmp_path):
    # Seen live: a session pickled by an older deploy crashed in report() AFTER the actions ran,
    # and the merchant was told "hiçbir şey yapılmadı". Both must not happen again.
    cfg("out_dir", tmp_path)
    old = MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s")
    del old._schema                                   # shape of a pre-SCHEMA pickle
    s = core.Session(sender="905321", backend=old)
    assert s.get_backend() is not old and s.backend._schema == MockBackend.SCHEMA

    monkeypatch.setattr(agent, "plan", _fake_plan)
    core.handle(s, "text", "Fular ekle 850 lira, gelmeyenlere haber ver")

    def boom(rep, lang="tr"):
        raise RuntimeError("model down")
    monkeypatch.setattr(report, "summarize_for_merchant", boom)
    r = core.handle(s, "text", "evet")
    assert "hiçbir şey" not in r[0].text
    assert "✅ Kataloğa eklendi: İpek Fular" in r[0].text and "Kampanya gönderildi" in r[0].text
    assert len(s.backend.campaigns) == 1


def test_core_answer_path_leaves_nothing_pending(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", lambda ctx, backend, max_revisions=3, lang="tr": SimpleNamespace(
        proposal=None, answer="Bugün 9 randevu, ciro 1300 TL.", tool_log=[], turns=1, usage={}))
    s = core.Session(sender="905321", backend=MockBackend("salon", today=date(2026, 10, 27), store_path=tmp_path / "s"))
    r = core.handle(s, "text", "Bugün ne oldu?")
    assert r[-1].text.startswith("Bugün 9 randevu") and s.pending is None
    assert "okuyamadım" not in core.handle(s, "text", "evet")[0].text or s.pending is None  # "evet" is harmless now


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
# conversation memory (task 05)
# --------------------------------------------------------------------------- #

def _salon(tmp_path):
    return core.Session(sender="905321",
                        backend=MockBackend("salon", today=date(2026, 10, 27), store_path=tmp_path / "s"))


def _answering(seen, text="Ayşe E.'nin randevusunu 17:15'e alayım mı?"):
    def _plan(ctx, backend, max_revisions=3, lang="tr"):
        seen.append(ctx)
        return SimpleNamespace(proposal=None, answer=text, tool_log=[], turns=1, usage={})
    return _plan


def test_core_history_records_owner_and_cirak_and_reaches_planner(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    seen = []
    monkeypatch.setattr(agent, "plan", _answering(seen))
    s = _salon(tmp_path)

    core.handle(s, "text", "Ayşe E.'nin randevusunu bir saat sonraya al")
    assert [(h["role"], h["text"]) for h in s.history] == [
        ("owner", "Ayşe E.'nin randevusunu bir saat sonraya al"),
        ("cirak", "Ayşe E.'nin randevusunu 17:15'e alayım mı?"),
    ]
    assert all(set(h) == {"role", "text", "at"} for h in s.history)
    assert seen[0].history == []                       # first message: nothing to remember yet

    core.handle(s, "text", "olur")                     # short follow-up, nothing pending → planner
    assert [h["role"] for h in s.history] == ["owner", "cirak", "owner", "cirak"]
    assert s.history[2]["text"] == "olur" and s.pending is None
    assert seen[1].history == s.history[:2]            # snapshot from before the current message
    assert seen[1].history is not s.history


def test_core_history_records_photos_voice_cards_and_reports(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    s = _salon(tmp_path)

    assert "1 fotoğraf aldım" in core.handle(s, "image", media_path="a.jpg")[0].text
    assert core.handle(s, "image", media_path="b.jpg") == []
    assert [(h["role"], h["text"]) for h in s.history] == [
        ("owner", "[1 fotoğraf]"), ("cirak", s.history[1]["text"]), ("owner", "[2 fotoğraf]")]
    assert "1 fotoğraf aldım" in s.history[1]["text"]

    monkeypatch.setattr(core.pipeline, "understand_media",
                        lambda text, audio, images, concept=None, out_dir=None:
                        core.pipeline.MediaContext(instruction="yarın boşluk var mı",
                                                   instruction_source="voice"))
    monkeypatch.setattr(agent, "plan", _answering([], text="Yarın 14:00 boş 👍"))
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep, lang="tr": "Hallettim.")
    core.handle(s, "audio", media_path="v.ogg")
    assert s.history[-2:] == [{"role": "owner", "text": "yarın boşluk var mı", "at": s.history[-2]["at"]},
                              {"role": "cirak", "text": "Yarın 14:00 boş 👍", "at": s.history[-1]["at"]}]

    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(core.pipeline, "understand_media",
                        lambda text, audio, images, concept=None, out_dir=None:
                        core.pipeline.MediaContext(instruction=text, instruction_source="text"))
    card = core.handle(s, "text", "Fular ekle 850 lira, gelmeyenlere haber ver")[-1].text
    assert s.history[-2]["text"] == "Fular ekle 850 lira, gelmeyenlere haber ver"
    assert s.history[-1] == {"role": "cirak", "text": card[:600], "at": s.history[-1]["at"]}
    assert len(s.history[-1]["text"]) <= 600

    summary = core.handle(s, "text", "Evet!")[0].text
    assert (s.history[-2]["text"], s.history[-1]["text"]) == ("Evet!", summary)
    assert s.history[-2]["role"] == "owner" and len(s.backend.campaigns) == 1


def test_core_history_trimmed_and_expired_dropped(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _answering([], text="ok"))
    s = _salon(tmp_path)
    now = time.time()
    s.history = ([{"role": "owner", "text": "stale", "at": now - 3 * 3600}]
                 + [{"role": "owner", "text": f"m{i}", "at": now - i} for i in range(12)])

    core.handle(s, "text", "yeni soru")
    assert len(s.history) == core.HISTORY_KEEP
    assert all(now - h["at"] <= core.HISTORY_TTL_S for h in s.history)
    assert [h["text"] for h in s.history[-2:]] == ["yeni soru", "ok"]


def test_core_history_cleared_by_reset_and_business_switch(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _answering([], text="ok"))
    s = _salon(tmp_path)
    core.handle(s, "text", "selam ver")
    assert len(s.history) == 2
    assert "Sıfırladım" in core.handle(s, "text", "/sifirla")[0].text and s.history == []
    core.handle(s, "text", "selam ver")
    assert len(s.history) == 2
    core.handle(s, "text", "/butik")
    assert s.history == [] and s.business == "butik"


def test_core_old_session_pickle_loads_with_empty_history():
    s = core.Session(sender="905321")
    s.__dict__.pop("history")                        # shape of a pre-history pickle
    assert pickle.loads(pickle.dumps(s)).history == []

    s = core.Session(sender="905321", history=[{"role": "owner", "text": "x", "at": 1.0}])
    assert pickle.loads(pickle.dumps(s)).history == s.history


# --------------------------------------------------------------------------- #
# /kedd mode (task 09)
# --------------------------------------------------------------------------- #

def test_kedd_command_switches_to_session_phone_backend(monkeypatch, cfg, tmp_path):
    cfg("kedd_api_url", "https://kedd.example")
    cfg("kedd_agent_secret", "s3cret")
    monkeypatch.setattr(KeddBackend, "store_profile", lambda self: {"business_name": "Demo Kuaför"})
    s = core.Session(sender="905000000000",
                     backend=MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s"))
    s.pending, s.images = {"at": time.time()}, ["a.jpg"]
    s.history = [{"role": "owner", "text": "x", "at": time.time()}]
    r = core.handle(s, "text", "/kedd")
    assert s.business == "kedd" and isinstance(s.backend, KeddBackend)
    assert s.backend._phone == "+905000000000"        # merchant comes from the chat, not the model
    assert s.pending is None and s.images == [] and s.history == []
    assert "KEDD'deki işletmen" in r[0].text and "Demo Kuaför" in r[0].text
    core.handle(s, "text", "/butik")                  # and back to the mock
    assert s.business == "butik" and isinstance(s.backend, MockBackend)


def test_kedd_command_unconfigured_stays_on_mock(cfg, tmp_path):
    cfg("kedd_api_url", "")
    cfg("kedd_agent_secret", "")
    s = _salon(tmp_path)
    before = (s.business, s.backend)
    r = core.handle(s, "text", "/kedd")
    assert r[0].text == "KEDD bağlantısı bu ortamda kapalı."
    assert (s.business, s.backend) == before and isinstance(s.backend, MockBackend)


def test_kedd_command_unreachable_gateway_still_switches(monkeypatch, cfg, tmp_path):
    cfg("kedd_api_url", "https://kedd.example")
    cfg("kedd_agent_secret", "s3cret")

    def boom(self):
        raise ValueError("KEDD'ye ulaşılamadı.")
    monkeypatch.setattr(KeddBackend, "store_profile", boom)
    s = _salon(tmp_path)
    r = core.handle(s, "text", "/kedd")
    assert s.business == "kedd" and isinstance(s.backend, KeddBackend)
    assert r[0].text == "Tamam, artık KEDD'deki işletmen için çalışıyorum."


def test_kedd_stale_backend_is_reseeded_with_session_phone(cfg):
    cfg("kedd_api_url", "https://kedd.example")
    cfg("kedd_agent_secret", "s3cret")
    old = KeddBackend("https://kedd.example", "s3cret", "+905321")
    del old._schema                                   # shape of a pre-SCHEMA pickle
    s = core.Session(sender="905321", business="kedd", backend=old)
    assert s.get_backend() is not old and s.backend._phone == "+905321"
    assert s.backend._schema == KeddBackend.SCHEMA


def test_session_holding_kedd_backend_pickles():
    s = core.Session(sender="905321", business="kedd",
                     backend=KeddBackend("https://kedd.example", "s3cret", "+905321"))
    s2 = pickle.loads(pickle.dumps(s))
    assert isinstance(s2.backend, KeddBackend) and s2.backend._phone == "+905321"
    assert s2.get_backend() is s2.backend             # schema matches: no reseed


def test_core_evet_without_pending_plans_instead_of_executing(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    s = _salon(tmp_path)
    n_items = len(s.backend.items)
    r = core.handle(s, "text", "olur")
    assert s.pending is not None and "onayına sunuyorum" in r[-1].text
    assert s.backend.campaigns == [] and len(s.backend.items) == n_items


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


def test_core_surfaces_pipeline_valueerror_to_owner(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    def bad_media(*a, **k):
        raise ValueError("Sesli not 3 dakikadan uzun, kısaltıp tekrar dener misin?")
    monkeypatch.setattr(core.pipeline, "understand_media", bad_media)
    s = core.Session(sender="905321", backend=MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s"))
    r = core.handle(s, "audio", media_path="x.ogg")
    assert r[-1].text.startswith("Sesli not 3 dakikadan uzun") and s.pending is None


# --------------------------------------------------------------------------- #
# approval integrity (security review H2 / L1 / M1)
# --------------------------------------------------------------------------- #

def _pending_session(monkeypatch, cfg, tmp_path, **kw):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep, lang="tr": "Hallettim.")
    s = core.Session(sender="905321", backend=MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s"))
    core.handle(s, "text", "Fular ekle 850 lira, gelmeyenlere haber ver", **kw)
    assert s.pending
    return s


def test_everyday_words_do_not_approve(monkeypatch, cfg, tmp_path):
    for word in ("tamam", "ok", "olur", "e", "y"):
        s = _pending_session(monkeypatch, cfg, tmp_path)
        core.handle(s, "text", word)
        assert s.backend.campaigns == [], word


def test_card_not_approvable_until_delivered_and_dropped_if_undelivered(monkeypatch, cfg, tmp_path):
    s = _pending_session(monkeypatch, cfg, tmp_path, async_delivery=True)
    r = core.handle(s, "text", "evet", sent_at=time.time(), async_delivery=True)
    assert "ulaşmadan" in r[0].text and s.backend.campaigns == [] and s.pending   # not shown yet → refused
    core.mark_delivered(s, False)
    assert s.pending is None                                                     # undelivered → dropped


def test_approval_sent_before_card_was_shown_is_refused(monkeypatch, cfg, tmp_path):
    s = _pending_session(monkeypatch, cfg, tmp_path, async_delivery=True)
    before = time.time() - 5
    core.mark_delivered(s, True)
    r = core.handle(s, "text", "evet", sent_at=before, async_delivery=True)
    assert "ulaşmadan" in r[0].text and s.backend.campaigns == []
    r = core.handle(s, "text", "evet", sent_at=time.time() + 1, async_delivery=True)
    assert r[0].text.startswith("Hallettim") and len(s.backend.campaigns) == 1


def test_consumed_approval_is_checkpointed_before_execution(monkeypatch, cfg, tmp_path):
    s = _pending_session(monkeypatch, cfg, tmp_path)
    seen = []
    real = core.executor.execute
    def spy(*a, **k):
        assert seen == ["saved"] and s.pending is None   # persisted as consumed before any side effect
        return real(*a, **k)
    monkeypatch.setattr(core.executor, "execute", spy)
    core.handle(s, "text", "evet", checkpoint=lambda: seen.append("saved"))
    assert len(s.backend.campaigns) == 1


def test_kedd_secret_is_not_pickled(cfg):
    import pickle
    from app.tools.backend import KeddBackend
    cfg("kedd_agent_secret", "s3cret-value-xyz")
    b = KeddBackend("https://gw.example", "s3cret-value-xyz", "+905000000000")
    blob = pickle.dumps(b)
    assert b"s3cret-value-xyz" not in blob
    assert pickle.loads(blob)._secret == b"s3cret-value-xyz"


# --------------------------------------------------------------------------- #
# task 15, items 12/15/16: Graph logs, download cap, dispatch-failure recovery
# --------------------------------------------------------------------------- #

def test_t15_graph_error_logs_status_and_code_only(monkeypatch, cfg, caplog):
    cfg("wa_access_token", "tok")
    cfg("wa_phone_number_id", OURS)

    class _Resp:
        status_code = 400
        text = '{"error": {"code": 131030, "type": "OAuthException", "message": "gizli-hata-mesajı"}}'

        def json(self):
            return json.loads(self.text)

        def raise_for_status(self):
            raise RuntimeError("HTTP 400")

    monkeypatch.setattr(wa.requests, "post", lambda *a, **k: _Resp())
    with caplog.at_level("ERROR", logger="app.channels.whatsapp"):
        try:
            wa._post_message({"to": "905321", "type": "text", "text": {"body": "x"}})
        except RuntimeError:
            pass
    assert "gizli-hata-mesajı" not in caplog.text
    assert "131030" in caplog.text and "400" in caplog.text


def test_t15_download_media_refuses_over_16mb(monkeypatch, cfg, tmp_path):
    cfg("wa_access_token", "tok")

    class _Meta:
        def raise_for_status(self):
            pass

        def json(self):
            return {"url": "https://graph/x", "mime_type": "image/jpeg"}

    class _Blob:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
        headers = {}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=65536):
            yield b"x" * (17 * 1024 * 1024)

    monkeypatch.setattr(wa.requests, "get",
                        lambda *a, **k: _Meta() if k.get("stream") is not True else _Blob())
    with pytest.raises(ValueError, match="16 MB"):
        wa.download_media("mid1", tmp_path)


def test_t15_download_media_streams_small_files(monkeypatch, cfg, tmp_path):
    cfg("wa_access_token", "tok")

    class _Meta:
        def raise_for_status(self):
            pass

        def json(self):
            return {"url": "https://graph/x", "mime_type": "audio/ogg; codecs=opus"}

    class _Blob:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
        headers = {"Content-Length": "4"}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=65536):
            yield b"OggS"

    calls = {}

    def fake_get(url, **k):
        calls[url] = k
        return _Meta() if k.get("stream") is not True else _Blob()

    monkeypatch.setattr(wa.requests, "get", fake_get)
    path = wa.download_media("mid9", tmp_path)
    assert path.read_bytes() == b"OggS" and path.suffix == ".ogg"
    assert calls["https://graph/x"].get("stream") is True


def _signed(client_payload: bytes):
    return "sha256=" + hmac.new(SECRET.encode(), client_payload, hashlib.sha256).hexdigest()


def test_t15_dispatch_failure_unmarks_for_redelivery(cfg):
    cfg("wa_verify_token", "vt")
    cfg("meta_app_secret", SECRET)
    cfg("wa_phone_number_id", OURS)
    cfg("wa_allowed_senders", frozenset({"905321"}))
    seen, unmarked, calls = set(), [], []

    def dispatch(msg):
        calls.append(msg["id"])
        if len(calls) == 1:
            raise RuntimeError("spawn exploded")

    def first_time(mid):
        if mid in seen:
            return False
        seen.add(mid)
        return True

    c = TestClient(server.create_app(dispatch, first_time, unmark=lambda mid: (seen.discard(mid),
                                                                                unmarked.append(mid))))
    body = json.dumps(_payload([
        {"id": "m1", "from": "905321", "timestamp": "1", "type": "text",
         "text": {"body": "selam"}}])).encode()
    assert c.post("/webhook", content=body, headers={"x-hub-signature-256": _signed(body)}).status_code == 500
    assert unmarked == ["m1"] and "m1" not in seen
    assert c.post("/webhook", content=body, headers={"x-hub-signature-256": _signed(body)}).status_code == 200
    assert calls == ["m1", "m1"]  # Meta's redelivery is processed, not dropped as a duplicate


def test_t15_non_object_json_is_400(cfg):
    cfg("wa_verify_token", "vt")
    cfg("meta_app_secret", SECRET)
    cfg("wa_phone_number_id", OURS)
    cfg("wa_allowed_senders", frozenset({"905321"}))
    c = TestClient(server.create_app(lambda m: None, lambda mid: True))
    for raw in (b"[1, 2]", b'"selam"', b"42"):
        assert c.post("/webhook", content=raw,
                      headers={"x-hub-signature-256": _signed(raw)}).status_code == 400


# --------------------------------------------------------------------------- #
# task 14: the social visual exists before the owner approves
# --------------------------------------------------------------------------- #

def _social_plan(monkeypatch, publish_at=None):
    action = {"type": "publish_social_post", "caption": "Yeni fularlar! #yenisezon",
              "platforms": ["instagram"], "visual_prompt": "silk scarves"}
    if publish_at:
        action["publish_at"] = publish_at

    def fake_plan(ctx, backend, max_revisions=2, lang="tr"):
        return SimpleNamespace(turns=1, usage={}, answer=None, tool_log=[],
                               proposal={"summary_tr": "Instagram gönderisi.", "actions": [dict(action)]})

    monkeypatch.setattr(agent, "plan", fake_plan)


def test_t14_social_preview_generated_before_card_and_attached(monkeypatch, cfg, tmp_path):
    from pathlib import Path

    from PIL import Image as PILImage
    cfg("out_dir", tmp_path)
    _social_plan(monkeypatch)
    seen = {}

    def fake_visual(imgs, prompt, out_path, **kw):
        seen["crops"] = len(imgs)
        PILImage.new("RGB", (8, 8)).save(out_path)
        return Path(out_path)

    monkeypatch.setattr("app.executor.meta.generate_campaign_visual", fake_visual)
    s = core.Session(sender="905321",
                     backend=MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s"))
    r = core.handle(s, "text", "Fularlar için Instagram gönderisi hazırla")
    assert s.pending is not None
    assert r[-1].image_path and Path(r[-1].image_path).is_file()   # the card carries the visual
    assert "📱 Instagram gönderisi" in r[-1].text
    assert "Yeni fularlar! #yenisezon" in r[-1].text                # caption verbatim
    assert s.pending["proposal"]["actions"][0]["_preview_path"] == r[-1].image_path
    assert seen == {"crops": 0}                                     # no photos → prompt-only visual


def test_t14_social_preview_uses_photo_crops(monkeypatch, cfg, tmp_path):
    from pathlib import Path

    from PIL import Image as PILImage

    from app.meta_client import Box
    cfg("out_dir", tmp_path)

    def fake_plan(ctx, backend, max_revisions=2, lang="tr"):
        assert [p.temp_id for p in ctx.products] == ["tmp1"]
        return SimpleNamespace(
            turns=1, usage={}, answer=None, tool_log=[],
            proposal={"summary_tr": "Instagram gönderisi.", "actions": [
                {"type": "publish_social_post", "caption": "Çantalar! #yeni",
                 "platforms": ["instagram", "facebook"], "visual_prompt": "leather bags",
                 "item_refs": ["tmp1"]}]})

    monkeypatch.setattr(agent, "plan", fake_plan)
    seen = {}

    def fake_visual(imgs, prompt, out_path, **kw):
        seen["crops"] = len(imgs)
        PILImage.new("RGB", (8, 8)).save(out_path)
        return Path(out_path)

    monkeypatch.setattr("app.executor.meta.generate_campaign_visual", fake_visual)
    monkeypatch.setattr("app.pipeline.infer_concept", lambda instruction: "bag")
    monkeypatch.setattr("app.pipeline.meta.segment",
                        lambda img, concept: [Box(0, 0, 0, 4, 4, 10, 10, 0.9)])
    monkeypatch.setattr("app.pipeline.meta.describe_image", lambda img, prompt: '{"name": "Çanta"}')
    photo = tmp_path / "bag.jpg"
    PILImage.new("RGB", (10, 10)).save(photo)
    s = core.Session(sender="905321",
                     backend=MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s"))
    core.handle(s, "image", "", media_path=str(photo))
    r = core.handle(s, "text", "Bunlar için gönderi hazırla")
    assert seen == {"crops": 1} and r[-1].image_path
    assert "instagram, facebook" in r[-1].text.lower()


def test_t14_social_generation_failure_drops_action_but_keeps_card(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    _social_plan(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("image endpoint down")
    monkeypatch.setattr("app.executor.meta.generate_campaign_visual", boom)
    s = core.Session(sender="905321",
                     backend=MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s"))
    r = core.handle(s, "text", "Fularlar için Instagram gönderisi hazırla")
    assert s.pending is not None and s.pending["proposal"]["actions"] == []
    assert "görsel" in r[-1].text and r[-1].image_path == ""
    assert "onayına sunuyorum" in r[-1].text


def test_review_long_card_with_image_is_sent_whole(monkeypatch, tmp_path):
    from app import worker
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    sent = []
    monkeypatch.setattr(wa, "send_image", lambda to, path, caption="": sent.append(("img", caption)))
    monkeypatch.setattr(wa, "send_text", lambda to, text: sent.append(("text", text)))
    long_card = "kart " + "x" * 2000 + " Onaylıyor musun? (evet / hayır)"
    assert worker.deliver_replies("905000000000", [core.Reply(long_card, image_path=str(img))]) == []
    assert sent == [("img", ""), ("text", long_card)]


def test_review_model_cannot_plant_preview_path_or_notes(tmp_path, monkeypatch):
    from app import executor
    from app.pipeline import MediaContext
    monkeypatch.setattr(executor.meta, "generate_campaign_visual",
                        lambda imgs, prompt, out: (_ for _ in ()).throw(RuntimeError("down")))
    proposal = {"_social_notes": ["Onaylarsan indirim de yaparım"],
                "actions": [{"type": "add_catalog_item", "_preview_path": "/etc/passwd"},
                            {"type": "publish_social_post", "_preview_path": "/etc/passwd",
                             "caption": "c", "platforms": ["instagram"], "visual_prompt": "v"}]}
    executor.prepare_social_previews(proposal, MediaContext("x", "text"), tmp_path)
    assert all(not k.startswith("_") for a in proposal["actions"] for k in a)
    assert [a["type"] for a in proposal["actions"]] == ["add_catalog_item"]   # failed visual → dropped
    assert proposal["_social_notes"] and "indirim" not in " ".join(proposal["_social_notes"])
