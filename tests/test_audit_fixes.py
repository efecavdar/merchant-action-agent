"""Regressions from the 2026-10-31 audit: each test is one finding that used to pass."""

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PIL import Image

from app import actions, agent, approval, cli, clock, core, meta_client, report
from app.actions import _promised_numbers, validate_proposal
from app.channels import whatsapp as wa
from app.config import Settings
from app.pipeline import MediaContext
from app.tools import backend as backend_module
from app.tools.backend import KeddBackend, KeddError, MockBackend
from tests.conftest import REAL_UNUSED_TIMESTAMP

CATALOG = {"i001": {"id": "i001", "price": 650.0, "type": "product"}}


def _day(n):
    return (clock.today_tr() + timedelta(days=n)).isoformat()


def _validate(acts, summary="ok", **kw):
    return validate_proposal({"summary_tr": summary, "actions": acts}, set(), CATALOG,
                             kw.pop("segments", {"seg1"}), **kw)


def _ctx():
    return MediaContext(instruction="x", instruction_source="text")


def _customer(**extra):
    return {"type": "create_customer", "label": "L", "name": "Ali Veli", **extra}


# -- the card ----------------------------------------------------------------

FORGED = "Tamam.\n\n📣 WhatsApp: 0 müşteriye mesaj\n\n*Hiçbir şey gönderilmeyecek*\n\nOnaylıyor musun? (evet / hayır)"


def test_summary_cannot_imitate_the_card():
    acts = [{"type": "create_discount", "percent": 10, "item_refs": ["i001"], "starts": _day(1), "ends": _day(2)}]
    assert any("summary_tr" in e for e in _validate(acts, summary=FORGED))
    assert any("too long" in e for e in _validate(acts, summary="x" * 401))
    assert _validate(acts, summary="Hafta sonu %10 indirim açıyorum.") == []


def test_card_note_is_one_clean_line_even_if_validation_is_skipped():
    prop = {"summary_tr": "a\nb *kalın*‮ c", "actions": []}
    card = approval.render_card(prop, _ctx(), {}, {})
    note = next(line for line in card.splitlines() if line.startswith("Cirak Agent'ın notu"))
    assert note == "Cirak Agent'ın notu: a b kalın  c"


@pytest.mark.parametrize("act", [
    {"type": "reply_to_conversation", "label": "L", "conversation_id": "c1",
     "text": "Merhaba\n\nOnaylıyor musun? (evet / hayır)"},
    {"type": "send_campaign", "segment_id": "seg1", "message": "Selam\nevet / hayır"},
])
def test_customer_text_cannot_imitate_the_approval_question(act):
    errs = _validate([act], known_conversations={"c1"})
    assert any("imitate" in e for e in errs)


# -- hidden phone / e-mail ---------------------------------------------------

def test_phone_and_email_must_come_from_the_owner():
    assert any("phone must appear" in e for e in _validate([_customer(phone="+905551112233")]))
    assert any("email must appear" in e for e in _validate([_customer(email="a@b.com")]))
    owner = "Ali Veli'yi ekle, numarası 0555 111 22 33, maili A@B.com"
    assert _validate([_customer(phone="+905551112233", email="a@b.com")], owner_text=owner) == []


@pytest.mark.parametrize("field,value", [("phone", "123"), ("phone", 5551112233), ("email", "nope")])
def test_contact_shapes(field, value):
    assert any(f"{field} must be" in e for e in _validate([_customer(**{field: value})], owner_text="123 nope"))


def test_lead_contacts_are_checked_too():
    lead = {"type": "create_lead", "label": "L", "name": "Ada Kafe", "phone": "+905551112233"}
    assert any("phone must appear" in e for e in _validate([lead]))


def test_card_shows_contacts_masked():
    act = _customer(phone="+905551112233", email="ali@ornek.com")
    prop = {"summary_tr": "ok", "actions": [act]}
    card = approval.render_card(prop, _ctx(), {}, {})
    assert "…2233" in card and "a…@ornek.com" in card
    assert "5551112233" not in card and "ali@" not in card


# -- CLI ---------------------------------------------------------------------

def test_cli_auto_approve_refuses_a_live_backend(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "get_backend", lambda _b: SimpleNamespace(name="kedd"))
    monkeypatch.setattr(cli.pipeline, "understand_media",
                        lambda *a, **k: pytest.fail("must stop before any work"))
    args = argparse.Namespace(out=str(tmp_path), business=None, auto_approve=True)
    assert cli.cmd_run(args) == 2
    assert "mock" in capsys.readouterr().out


def test_console_approval_is_as_strict_as_chat(monkeypatch):
    for word, want in (("evet", True), ("tamam", False), ("ok", False), ("e", False)):
        monkeypatch.setattr("builtins.input", lambda _p: word)
        assert approval.ask_console("kart") is want


# -- number guard ------------------------------------------------------------

@pytest.mark.parametrize("text,pcts,prices", [
    ("hafta sonu 15% indirim", {15}, set()),
    ("yüzde15 indirim", {15}, set()),
    ("% 20 indirim", {20}, set()),
    ("₺850", set(), {850.0}),
    ("₺ 1.290 sadece", set(), {1290.0}),
    ("850,00 TL", set(), {850.0}),
    ("850.50 TL", set(), {850.5}),
    ("1.290,50 ₺", set(), {1290.5}),
    ("Çanta,850 TL", set(), {850.0}),
    ("850 %15 indirimle", {15}, set()),
])
def test_promised_numbers_spellings(text, pcts, prices):
    assert _promised_numbers(text) == (pcts, prices)


def test_postfix_percent_without_a_discount_is_refused():
    errs = _validate([{"type": "send_campaign", "segment_id": "seg1", "message": "Hafta sonu 15% indirim"}])
    assert any("%15" in e for e in errs)


# -- types and shapes --------------------------------------------------------

def _item(**extra):
    return {"type": "add_catalog_item", "item_type": "product", "ref": "n1", "name": "Fular", "price": 100, **extra}


@pytest.mark.parametrize("price", [float("nan"), float("inf"), True, "1.500", "1,500", "abc"])
def test_bad_prices_are_refused(price):
    assert any("price must be" in e for e in _validate([_item(price=price)]))


@pytest.mark.parametrize("field", ["name", "description", "category"])
def test_catalog_text_fields_must_be_strings(field):
    assert any(f"{field} must be a string" in e for e in _validate([_item(**{field: ["x"]})]))


def test_percent_true_is_not_one():
    acts = [{"type": "create_discount", "percent": True, "item_refs": ["i001"],
             "starts": _day(1), "ends": _day(2)}]
    assert any("percent must be" in e for e in _validate(acts))


@pytest.mark.parametrize("prop", [
    {"summary_tr": ["x"], "actions": [_item()]},
    {"summary_tr": "ok", "actions": ["kitap"]},
    {"summary_tr": "ok", "actions": [{"type": "create_discount", "percent": 10, "item_refs": 5,
                                      "starts": None, "ends": 7}]},
    {"summary_tr": "ok", "actions": [{"type": "send_campaign", "segment_id": "seg1", "message": ["a"]}]},
    {"summary_tr": "ok", "actions": [{"type": "publish_social_post", "caption": {"a": 1}, "platforms": ["instagram"],
                                      "visual_prompt": "x", "item_refs": [["nested"]]}]},
])
def test_malformed_shapes_become_errors_not_crashes(prop):
    assert validate_proposal(prop, set(), CATALOG, {"seg1"})


def test_a_crashing_validator_is_a_revision_not_an_outage(monkeypatch):
    backend = MockBackend("butik")
    calls = [SimpleNamespace(type="function_call", name="propose_actions", arguments='{"actions": ["x"]}',
                             call_id="1")]
    good = SimpleNamespace(type="function_call", name="reply_only", arguments='{"text": "tamam"}', call_id="2")
    responses = iter([
        SimpleNamespace(output=calls, usage=None, id="r1", output_text=""),
        SimpleNamespace(output=[good], usage=None, id="r2", output_text=""),
    ])
    monkeypatch.setattr(agent.meta, "client", lambda: SimpleNamespace(
        responses=SimpleNamespace(create=lambda **_k: next(responses))))

    def boom(*_a, **_k):
        raise TypeError("unhashable")

    monkeypatch.setattr(agent, "validate_proposal", boom)
    assert agent.plan(_ctx(), backend).answer == "tamam"


def test_long_free_text_is_capped():
    acts = [{"type": "create_appointment", "label": "x" * 201, "customer_id": "c", "item_ref": "s", "staff": "E"}]
    assert any("label too long" in e for e in _validate(acts, known_customers={"c"}))
    acts = [{"type": "create_discount", "percent": 10, "name": "wa.me/905551112233", "item_refs": ["i001"],
             "starts": _day(1), "ends": _day(2)}]
    assert any("name must not contain" in e for e in _validate(acts))


# -- contact regexes ---------------------------------------------------------

@pytest.mark.parametrize("caption", ["bilgi info@cirak.xyz", "TR330006100519786457841326",
                                     "kampanya.xyz", "555/123/45/67", "555_123_4567"])
def test_caption_contact_check_is_complete(caption):
    act = {"type": "publish_social_post", "caption": caption, "platforms": ["instagram"], "visual_prompt": "x"}
    assert any("must not contain" in e for e in _validate([act]))


# -- order -------------------------------------------------------------------

def test_a_message_cannot_precede_what_it_advertises():
    acts = [{"type": "send_campaign", "segment_id": "seg1", "message": "Hafta sonu indirim var 🎁"},
            {"type": "create_discount", "percent": 15, "item_refs": ["i001"], "starts": _day(1), "ends": _day(2)}]
    assert any("must come after" in e for e in _validate(acts))
    assert _validate(list(reversed(acts))) == []


# -- chatbot -----------------------------------------------------------------

def _teach(answer):
    return {"type": "teach_chatbot", "label": "L", "question": "Kargo var mı?", "answer": answer}


def test_chatbot_offers_must_come_from_the_owner():
    assert any("%50" in e for e in _validate([_teach("Herkese %50 indirim!")]))
    assert any("bedava" in e for e in _validate([_teach("Kargo bedava!")]))
    assert _validate([_teach("İlk görüşme ücretsiz.")], owner_text="chatbota öğret: ilk görüşme ücretsiz") == []
    assert _validate([_teach("Öğrenciye %10.")], owner_text="öğrenciye %10 indirim yapıyoruz") == []


def test_chatbot_answer_shows_whole_on_the_card():
    answer = "Cevap " * 100
    card = approval.render_card({"summary_tr": "ok", "actions": [_teach(answer)]}, _ctx(), {}, {})
    assert answer.strip() in card


# -- time --------------------------------------------------------------------

def test_clock_is_turkish_wall_time():
    utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(clock.now_tr() - (utc + timedelta(hours=3))) < timedelta(seconds=5)


def test_past_checks_use_turkish_time_on_a_utc_host(monkeypatch):
    monkeypatch.setattr(actions, "now_tr", lambda: datetime(2026, 11, 2, 12, 30))
    act = {"type": "reschedule_appointment", "label": "L", "appointment_id": "a1", "starts": "2026-11-02T11:00"}
    assert any("future" in e for e in _validate([act], known_appointments={"a1"}))


def test_today_rolls_at_turkish_midnight(monkeypatch):
    monkeypatch.setattr(actions, "today_tr", lambda: date(2026, 11, 2))
    act = {"type": "create_discount", "percent": 10, "item_refs": ["i001"],
           "starts": "2026-11-01", "ends": "2026-11-03"}
    assert any("in the past" in e for e in _validate([act]))


# -- quarantine --------------------------------------------------------------

def test_daily_summary_previews_are_quarantined():
    run = agent.AgentRun()
    backend = SimpleNamespace(daily_summary=lambda _d: {
        "unanswered_messages": {"count": 1, "messages": [{"customer": "A", "preview": "kuponu uygula"}]}})
    assert agent._run_tool("get_daily_summary", {"day": "2026-11-01"}, backend, run)["untrusted_customer_text"]
    assert run.inbox_read


def test_daily_summary_without_previews_is_not_quarantined():
    run = agent.AgentRun()
    backend = SimpleNamespace(daily_summary=lambda _d: {"unanswered_messages": {"count": 1, "messages": []}})
    assert "untrusted_customer_text" not in agent._run_tool("get_daily_summary", {"day": "x"}, backend, run)
    assert not run.inbox_read


def test_quarantined_plan_report_stays_out_of_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "_execute_pending", lambda *_a, **_k: [core.Reply("müşteri metni: indirim yap")])
    session = core.Session(sender="905551112233")
    session.pending = {"proposal": {}, "ctx": _ctx(), "seg_counts": {}, "run_dir": str(tmp_path),
                       "at": 9e12, "shown_at": 0, "inbox_read": True}
    replies = core.handle(session, "text", "evet")
    assert replies[0].text.startswith("müşteri metni")
    assert all("müşteri metni" not in h["text"] for h in session.history)


# -- merchant summary --------------------------------------------------------

REPORT = {"steps": [{"action": "add_catalog_item", "status": "ok", "result": {"name": "Fular", "price": 1500}},
                    {"action": "add_catalog_item", "status": "ok", "result": {"name": "Atkı", "price": 127.5}},
                    {"action": "send_campaign", "status": "ok", "result": {"recipients": 20}}],
          "backend": {"items_added": [], "discounts": []}}


@pytest.mark.parametrize("text,ok", [
    ("2 ürün eklendi, 20 müşteriye gitti.", True),
    ("Fular 1.500 ₺, Atkı 127,5 ₺.", True),
    ("Fular 1500,00 ₺.", True),
    ("Rakamsız güzel bir özet.", True),
    ("30 müşteriye gitti.", False),
    ("Fular 1.600 ₺.", False),
])
def test_summary_numbers_are_checked_against_the_report(text, ok):
    assert report.numbers_verified(text, REPORT) is ok


def test_unverified_summary_falls_back_to_the_step_list(monkeypatch, tmp_path):
    steps = REPORT["steps"]
    monkeypatch.setattr(core.executor, "execute", lambda *_a, **_k: steps)
    monkeypatch.setattr(core.report, "build_report", lambda s, _b: {"steps": s, "backend": {}})
    monkeypatch.setattr(core.report, "summarize_for_merchant", lambda _r, lang="tr": "999 müşteriye gitti")
    session = core.Session(sender="905551112233", backend=MockBackend("butik"))
    session.pending = {"proposal": {"actions": []}, "ctx": _ctx(), "seg_counts": {}, "run_dir": str(tmp_path),
                       "at": 9e12, "shown_at": 0}
    text = core.handle(session, "text", "evet")[0].text
    assert "999" not in text and "Fular" in text


# -- KEDD adapter ------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code, self._body, self._bad = status, body, bad_json

    def json(self):
        if self._bad:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


def _kb(*responses):
    kb = KeddBackend("https://kedd.example", "s", "+905321")
    queue = list(responses)
    kb._s = SimpleNamespace(get=lambda *_a, **_k: queue.pop(0), post=lambda *_a, **_k: queue.pop(0))
    return kb


def test_a_proxy_page_is_a_kedd_error_not_a_parser_message():
    with pytest.raises(KeddError, match="beklenmedik") as e:
        _kb(_Resp(bad_json=True))._get("/agent/profile")
    assert "Expecting value" not in str(e.value)
    with pytest.raises(KeddError, match="belirsiz"):
        _kb(_Resp(bad_json=True))._post("/agent/catalog", {"name": "x"})


def test_partial_discount_says_what_is_already_live():
    kb = _kb(_Resp(body={"sale_price": 90}), _Resp(status=409, body={"error": "x", "message": "olmadı"}))
    with pytest.raises(KeddError, match="1/2 ürüne uygulandı") as e:
        kb.create_discount("i", 10, ["a", "b"], "2026-11-02", "2026-11-03")
    assert "olmadı" in str(e.value)


def test_first_item_failure_is_a_plain_error():
    kb = _kb(_Resp(status=409, body={"error": "x", "message": "olmadı"}))
    with pytest.raises(KeddError) as e:
        kb.create_discount("i", 10, ["a", "b"], "2026-11-02", "2026-11-03")
    assert str(e.value).startswith("olmadı")


def test_identical_reads_from_two_sessions_never_sign_identically(monkeypatch):
    # The public demo: every visitor has a backend, all share one phone, and the gateway takes a signature once.
    monkeypatch.setattr(KeddBackend, "_unused_timestamp", REAL_UNUSED_TIMESTAMP)
    a, b = _kb(), _kb()
    clock_values = iter([1000.0, 1000.0, 1000.0, 1001.0])
    monkeypatch.setattr(backend_module.time, "time", lambda: next(clock_values))
    monkeypatch.setattr(backend_module.time, "sleep", lambda _s: None)
    backend_module._recent_signed.clear()
    first = a._headers("GET", "/agent/profile", "")
    second = b._headers("GET", "/agent/profile", "")
    assert first["X-Agent-Signature"] != second["X-Agent-Signature"]


@pytest.mark.parametrize("url", ["http://kedd.example", "ftp://kedd.example"])
def test_plain_http_gateway_is_refused(url):
    with pytest.raises(RuntimeError, match="https"):
        KeddBackend(url, "s", "+905321")


def test_localhost_gateway_may_be_http():
    assert KeddBackend("http://localhost:5001", "s", "+905321").base == "http://localhost:5001"


# -- media -------------------------------------------------------------------

def test_load_image_refuses_a_decompression_bomb_before_decoding(tmp_path, monkeypatch):
    p = tmp_path / "x.png"
    Image.new("RGB", (100, 100)).save(p)
    monkeypatch.setattr(meta_client, "MAX_PIXELS", 99 * 99)

    def no_decode(*_a, **_k):
        pytest.fail("pixels must not be decoded")

    monkeypatch.setattr(Image.Image, "convert", no_decode)
    with pytest.raises(ValueError, match="çok büyük"):
        meta_client.load_image(p)


def test_asr_html_page_reaches_the_owner_as_our_message(tmp_path, monkeypatch):
    wav = tmp_path / "v.wav"
    wav.write_bytes(b"x")
    monkeypatch.setattr(meta_client, "to_wav_24k_mono", lambda _p: wav)
    monkeypatch.setattr(meta_client, "_auth_headers", lambda: {})
    page = SimpleNamespace(raise_for_status=lambda: None, json=lambda: json.loads("<html>"))
    monkeypatch.setattr(meta_client.requests, "post", lambda *_a, **_k: page)
    with pytest.raises(ValueError) as e:
        meta_client.transcribe(wav)
    assert str(e.value) == meta_client._UNREADABLE_TR


def test_converted_audio_does_not_pile_up(tmp_path, monkeypatch):
    work = tmp_path / f"{meta_client._WAV_DIR_PREFIX}1"
    work.mkdir()
    wav = work / "v.wav"
    wav.write_bytes(b"x")
    monkeypatch.setattr(meta_client, "to_wav_24k_mono", lambda _p: wav)
    monkeypatch.setattr(meta_client, "_auth_headers", lambda: {})
    done = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"transcript": "selam"})
    monkeypatch.setattr(meta_client.requests, "post", lambda *_a, **_k: done)
    meta_client.transcribe(wav)
    assert not work.exists()


# -- WhatsApp ----------------------------------------------------------------

def _webhook(*messages):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "1"}, "messages": list(messages)}}]}]}


def test_reactions_are_not_messages():
    payload = _webhook({"id": "m1", "from": "905", "type": "reaction", "reaction": {"emoji": "👍"}},
                       {"id": "m2", "from": "905", "type": "system"},
                       {"id": "m3", "from": "905", "type": "sticker"})
    assert [m.id for m in wa.parse_webhook(payload, "1")] == ["m3"]


def test_non_ascii_signature_is_a_refusal_not_a_crash():
    assert wa.verify_signature(b"{}", "sha256=é", "secret") is False


# -- config ------------------------------------------------------------------

def test_contributor_models_are_refused():
    with pytest.raises(RuntimeError, match="contributor"):
        Settings(orchestrator_model="muse-spark-1.3-contributor")


# -- mock day ----------------------------------------------------------------

def _yesterdays_mock():
    b = MockBackend("butik")
    b.today -= timedelta(days=1)
    return b


def test_mock_session_follows_the_day():
    session = core.Session(sender="9055", business="butik", backend=_yesterdays_mock())
    assert session.get_backend().today == clock.today_tr()


def test_a_pending_card_keeps_its_data_over_midnight():
    old = _yesterdays_mock()
    session = core.Session(sender="9055", business="butik", backend=old, pending={"at": 9e12})
    assert session.get_backend() is old


def test_a_pinned_mock_stays_on_its_day():
    pinned = MockBackend("butik", today=date(2026, 1, 5))
    assert core.Session(sender="9055", business="butik", backend=pinned).get_backend() is pinned


def test_mock_store_file_has_no_phone_numbers(tmp_path):
    b = MockBackend("butik", store_path=tmp_path / "s.json")
    b.save()
    assert '"phone"' not in (tmp_path / "s.json").read_text()
