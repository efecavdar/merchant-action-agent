"""Task 23: the owner is answered in the language they write in (Turkish or English).

Customer-facing text (campaign messages, captions, catalog names/descriptions) stays Turkish;
every fixed owner-facing string follows `Session.lang`."""

import json
import pickle
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import agent, approval, brief, core, i18n, report
from app.pipeline import MediaContext
from app.tools.backend import MockBackend

TR_INSTRUCTION = ("Yeni ipek fular koleksiyonu ekle, tanesi 850 lira, hafta sonu %15 indirim, "
                  "gelmeyenlere haber ver")
EN_INSTRUCTION = ("Add a new silk scarf collection, 850 lira each, 15% off this weekend, tell customers "
                  "who haven't visited")
TR_MESSAGE = "Yeni ipek fularlar geldi, hafta sonu %15 indirimli!"


@pytest.fixture
def cfg():
    from app.config import settings
    saved = {}

    def _set(name, value):
        saved.setdefault(name, getattr(settings, name))
        object.__setattr__(settings, name, value)
    yield _set
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def _session(tmp_path, business="butik"):
    return core.Session(sender="905321",
                        backend=MockBackend(business, today=date(2026, 10, 27),
                                            store_path=tmp_path / "s"))


def _fake_plan(ctx, backend, max_revisions=2, lang="tr"):
    seg = backend.find_customers(inactive_days=60)
    return SimpleNamespace(
        turns=1, usage={}, answer=None,
        tool_log=[{"tool": "find_customers", "args": {}, "result": seg}],
        proposal={"summary_tr": "Fularlar eklendi, indirim ve kampanya hazır.", "actions": [
            {"type": "add_catalog_item", "item_type": "product", "ref": "n1",
             "name": "İpek Fular", "price": 850},
            {"type": "create_discount", "percent": 15, "item_refs": ["n1"],
             "starts": "2026-11-01", "ends": "2026-11-02"},
            {"type": "send_campaign", "segment_id": seg["segment_id"], "message": TR_MESSAGE},
        ]},
    )


# --------------------------------------------------------------------------- #
# detector
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text", [
    TR_INSTRUCTION,
    "Fular ekle 850 lira, gelmeyenlere haber ver",
    "Bugün ne oldu?",
    "merhaba",
    "Ayşe E.'nin randevusunu bir saat sonraya al",
    "Fularlar için Instagram gönderisi hazırla",
    "Bunlar için gönderi hazırla",
])
def test_detect_turkish(text):
    assert i18n.detect_lang(text, "en") == "tr"


@pytest.mark.parametrize("text", [
    EN_INSTRUCTION,
    "Book Ayşe at 3 o'clock",
    "What happened today?",
    "Show me tomorrow's free slots",
    "I have free slots tomorrow afternoon, 20% off hair coloring, tell loyal customers.",
])
def test_detect_english(text):
    assert i18n.detect_lang(text, "tr") == "en"


@pytest.mark.parametrize("text", ["the ve", "merhaba hello", "👍🎉", "📷", "yes", "evet", "no", "ok"])
@pytest.mark.parametrize("prev", ["tr", "en"])
def test_detect_ties_and_control_words_keep_previous(text, prev):
    assert i18n.detect_lang(text, prev) == prev


def test_strings_fall_back_to_turkish():
    assert i18n.t("help", "xx") == i18n.t("help", "tr")
    assert i18n.t("step.no_such_action", "en", "raw") == "raw"


# --------------------------------------------------------------------------- #
# cards: same facts, translated labels, Turkish customer quotes
# --------------------------------------------------------------------------- #

def _card_for(monkeypatch, cfg, tmp_path, instruction):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    s = _session(tmp_path)
    card = core.handle(s, "text", instruction)[-1].text
    assert s.pending
    return s, card


def test_english_instruction_gives_english_card_with_same_facts(monkeypatch, cfg, tmp_path):
    s_tr, tr_card = _card_for(monkeypatch, cfg, tmp_path, TR_INSTRUCTION)
    s_en, en_card = _card_for(monkeypatch, cfg, tmp_path, EN_INSTRUCTION)
    assert (s_tr.lang, s_en.lang) == ("tr", "en")

    for fact in ("850", "İpek Fular", "%15", "2026-11-01", "2026-11-02", TR_MESSAGE):
        assert fact in tr_card and fact in en_card
    count = re.search(r"(\d+) müşteriye mesaj", tr_card).group(1)
    assert f"message to {count} customers" in en_card

    assert "onayına sunuyorum" in tr_card and "Onaylıyor musun? (evet / hayır)" in tr_card
    assert "Ürün:" in tr_card and "İndirim:" in tr_card
    assert "for your approval" in en_card and "Approve? (yes / no)" in en_card
    assert "Product:" in en_card and "Discount:" in en_card
    assert "Approve?" not in tr_card and "Onaylıyor" not in en_card

    assert f"“{TR_MESSAGE}” (müşteriye Türkçe gider)" in tr_card
    assert f"“{TR_MESSAGE}” (sent to customers in Turkish)" in en_card


def test_social_caption_stays_turkish_in_both_cards():
    prop = {"summary_tr": "x", "actions": [
        {"type": "publish_social_post", "caption": "Yeni fularlar! #yenisezon",
         "platforms": ["instagram"], "visual_prompt": "silk scarves", "publish_at": "2026-10-31T15:00"}]}
    ctx = MediaContext("x", "text")
    tr_card = approval.render_card(prop, ctx, {})
    en_card = approval.render_card(prop, ctx, {}, lang="en")
    assert "📱 Instagram gönderisi · 31.10 15:00'de paylaşılacak" in tr_card
    assert "📱 Instagram post · will be posted at 31.10 15:00" in en_card
    assert "“Yeni fularlar! #yenisezon” (müşteriye Türkçe gider)" in tr_card
    assert "“Yeni fularlar! #yenisezon” (sent to customers in Turkish)" in en_card


def test_fact_lines_translate_labels_keep_facts():
    log = [
        {"tool": "preview_bulk_price", "result": {
            "preview_id": "pv1", "items": [{"name": "Midi Elbise", "old_price": 1290,
                                            "new_price": 1419}]}},
        {"tool": "find_loyalty_member", "result": {
            "members": [{"member_id": "c1", "name": "Ayşe Y.", "points": 450}]}},
        {"tool": "get_ads_performance", "result": {
            "campaigns": [{"id": "ad1", "name": "Yaz", "daily_budget": 500}]}},
        {"tool": "find_customer", "result": {"matches": [{"id": "c1", "name": "Ayşe Y."}]}},
        {"tool": "list_catalog", "result": {"items": [{"id": "i1", "name": "Saç Kesimi"}]}},
        {"tool": "find_appointments", "result": {"appointments": [
            {"id": "a1", "customer": "Ayşe A.", "service": "Saç Boyama",
             "starts": "2026-10-29T10:00"}]}},
    ]
    prop = {"summary_tr": "x", "actions": [
        {"type": "apply_bulk_price", "preview_id": "pv1"},
        {"type": "adjust_loyalty", "customer_id": "c1", "points": 200, "reason": "doğum günü",
         "label": "l"},
        {"type": "ads_action", "campaign_id": "ad1", "action": "increase_budget", "delta_pct": 10},
        {"type": "ads_action", "campaign_id": "ad1", "action": "pause"},
        {"type": "create_appointment", "customer_id": "c1", "item_ref": "i1", "staff": "Elif",
         "starts": "2026-10-31T15:00", "label": "l"},
        {"type": "reschedule_appointment", "appointment_id": "a1", "starts": "2026-10-29T11:00",
         "label": "l"},
        {"type": "cancel_appointment", "appointment_id": "a1", "label": "l"},
    ]}
    card = approval.render_card(prop, MediaContext("x", "text"), {}, approval.card_facts(log), lang="en")
    assert "💸 Bulk prices: 1 item — Midi Elbise 1,290→1,419 ₺" in card
    assert "⭐ Points: Ayşe Y. 450→650 (doğum günü)" in card
    assert "📢 Ad: Yaz — daily budget will increase by %10 (500→550 ₺)" in card
    assert "📢 Ad: Yaz — will be paused" in card
    assert "📅 Appointment: Ayşe Y. — Saç Kesimi, Elif, 31.10 15:00" in card
    assert "🔁 Appointment moved: Ayşe A. — Saç Boyama, 29.10 10:00 → 29.10 11:00" in card
    assert "❌ Appointment cancelled: Ayşe A. — Saç Boyama, 29.10 10:00" in card
    assert "Randevu" not in card and "Puan:" not in card and "Reklam:" not in card


# --------------------------------------------------------------------------- #
# approval, session shape, control words
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("instruction,lang", [(TR_INSTRUCTION, "tr"), (EN_INSTRUCTION, "en")])
def test_yes_approves_in_both_languages(monkeypatch, cfg, tmp_path, instruction, lang):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep, lang="tr": "Hallettim.")
    s = _session(tmp_path)
    core.handle(s, "text", instruction)
    assert s.lang == lang and s.pending
    core.handle(s, "text", "yes")
    assert s.pending is None and len(s.backend.campaigns) == 1 and s.lang == lang


def test_old_session_pickle_loads_lang_tr():
    assert core.Session(sender="905321").lang == "tr"
    s = core.Session(sender="905321")
    s.__dict__.pop("lang")                              # shape of a pre-lang pickle
    assert pickle.loads(pickle.dumps(s)).lang == "tr"


def test_control_words_do_not_change_language(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", lambda ctx, backend, max_revisions=3, lang="tr":
                        SimpleNamespace(proposal=None, answer="ok", tool_log=[], turns=1, usage={}))
    s = _session(tmp_path)
    s.lang = "en"
    core.handle(s, "text", "/butik")                    # command: no language signal
    assert s.lang == "en" and s.business == "butik"
    core.handle(s, "text", "evet")                      # nothing pending → planner, lang kept
    core.handle(s, "text", "hayır")
    assert s.lang == "en"


def test_status_lines_follow_language(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(core.pipeline, "understand_media",
                        lambda text, audio, images, concept=None, out_dir=None:
                        MediaContext(instruction=text, instruction_source="text"))
    s = _session(tmp_path)
    s.lang = "en"
    assert core.handle(s, "image", media_path="a.jpg")[0].text.startswith("📷 Got 1 photo.")
    assert "Nothing happens without your *yes*" in core.handle(s, "text", "/help")[0].text
    core.handle(s, "text", EN_INSTRUCTION)
    assert "OK, I did nothing" in core.handle(s, "text", "no")[0].text


def test_voice_transcript_sets_language(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)

    def _voice(transcript):
        def _plan(ctx, backend, max_revisions=3, lang="tr"):
            return SimpleNamespace(proposal=None, answer="ok", tool_log=[], turns=1, usage={})
        monkeypatch.setattr(
            core.pipeline, "understand_media",
            lambda text, audio, images, concept=None, out_dir=None:
            MediaContext(instruction=transcript, instruction_source="voice"))
        monkeypatch.setattr(agent, "plan", _plan)

    notes = []
    _voice("Add two red bags to the catalog")
    s = _session(tmp_path)
    core.handle(s, "audio", media_path="v.ogg", notify=notes.append)
    assert s.lang == "en" and notes[1].startswith("🎙️ I understood")

    _voice("yarın boşluk var mı")
    s2 = _session(tmp_path)
    s2.lang = "en"
    core.handle(s2, "audio", media_path="v.ogg")
    assert s2.lang == "tr"


# --------------------------------------------------------------------------- #
# agent prompt, report, brief
# --------------------------------------------------------------------------- #

class _FakeAnswer:
    def __init__(self):
        self.seen: list[dict] = []

    def create(self, **kw):
        self.seen.append(kw)
        return SimpleNamespace(
            id="r1", output=[SimpleNamespace(type="function_call", name="reply_only",
                                             arguments=json.dumps({"text": "ok"}), call_id="c1")],
            output_text="", usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def test_agent_prompt_carries_owner_language(monkeypatch):
    fake = _FakeAnswer()
    monkeypatch.setattr(agent.meta, "client", lambda: SimpleNamespace(responses=fake))
    backend = MockBackend("butik", today=date(2026, 10, 27))
    agent.plan(MediaContext("x", "text"), backend, lang="en")
    agent.plan(MediaContext("x", "text"), backend, lang="tr")
    tail = "Customer-facing texts (messages, captions, catalog names/descriptions) are always Turkish."
    assert f"Reply to the owner in English. {tail}" in fake.seen[0]["instructions"]
    assert f"Reply to the owner in Turkish. {tail}" in fake.seen[1]["instructions"]


def test_report_honours_language(monkeypatch):
    prompts = []
    monkeypatch.setattr(report.meta, "chat",
                        lambda prompt, effort="minimal": prompts.append(prompt) or "ok")
    report.summarize_for_merchant({"steps": []}, lang="en")
    report.summarize_for_merchant({"steps": []}, lang="tr")
    assert "English WhatsApp message" in prompts[0] and "'simulated' once" in prompts[0]
    assert "Turkish WhatsApp message" in prompts[1] and "'simülasyon' once" in prompts[1]


def test_report_gets_session_language_end_to_end(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    seen = []
    monkeypatch.setattr(report, "summarize_for_merchant",
                        lambda rep, lang="tr": seen.append(lang) or "ok")
    s = _session(tmp_path)
    core.handle(s, "text", EN_INSTRUCTION)
    core.handle(s, "text", "yes")
    assert seen == ["en"]


def test_brief_honours_language(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    seen = []

    def _plan(ctx, backend, max_revisions=3, lang="tr"):
        seen.append(ctx)
        return SimpleNamespace(proposal=None, answer="9 appointments today.", tool_log=[], turns=1,
                               usage={})
    monkeypatch.setattr(agent, "plan", _plan)

    s = _session(tmp_path, business="salon")
    s.lang = "en"
    replies = brief.build_morning_brief(s)
    assert seen[0].instruction == brief.BRIEF_INSTRUCTION_EN
    assert replies[0].text.startswith("Good morning ☀️ ")

    s2 = _session(tmp_path, business="salon")
    replies2 = brief.build_morning_brief(s2)
    assert seen[1].instruction == brief.BRIEF_INSTRUCTION
    assert replies2[0].text.startswith("Günaydın ☀️ ")


def test_brief_card_in_english_still_needs_yes(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep, lang="tr": "Done.")
    s = _session(tmp_path)
    s.lang = "en"
    r = brief.build_morning_brief(s)
    assert s.pending is not None and "for your approval" in r[0].text
    assert "before the card reached you" in core.handle(s, "text", "yes")[0].text
    core.mark_delivered(s, True)
    assert core.handle(s, "text", "yes")[0].text.startswith("Done.")


# --------------------------------------------------------------------------- #
# nothing customer-facing changes language
# --------------------------------------------------------------------------- #

def test_nothing_customer_facing_changes_language(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep, lang="tr": "Done.")
    seen = []
    real = core.executor.execute

    def spy(*a, **k):
        seen.append(a[0])                                # the proposal the executor runs
        return real(*a, **k)
    monkeypatch.setattr(core.executor, "execute", spy)

    s = _session(tmp_path)
    core.handle(s, "text", EN_INSTRUCTION)
    assert s.lang == "en"
    core.handle(s, "text", "yes")

    by_type = {a["type"]: a for a in seen[0]["actions"]}
    assert by_type["send_campaign"]["message"] == TR_MESSAGE
    assert by_type["add_catalog_item"]["name"] == "İpek Fular"
    assert s.backend.campaigns[0].message == TR_MESSAGE
    assert s.backend.items[-1].name == "İpek Fular"


def test_quick_chips_have_english_versions():
    src = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text()
    assert EN_INSTRUCTION in src
    assert "paintQuick" in src
