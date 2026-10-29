"""Task 25: step-by-step progress during execution, live on every channel."""

import re
import time
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import agent, core, executor, i18n, report
from app.channels import web as webchan
from app.config import settings
from app.pipeline import MediaContext
from app.tools.backend import KeddBackend, MockBackend

TR_INSTRUCTION = ("Yeni ipek fular koleksiyonu ekle, tanesi 850 lira, hafta sonu %15 indirim, "
                  "gelmeyenlere haber ver")
EN_INSTRUCTION = ("Add a new silk scarf collection, 850 lira each, 15% off this weekend, tell customers "
                  "who haven't visited")


@pytest.fixture
def cfg():
    saved = {}

    def _set(name, value):
        saved.setdefault(name, getattr(settings, name))
        object.__setattr__(settings, name, value)
    yield _set
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def _butik(tmp_path):
    return MockBackend("butik", today=date(2026, 10, 27), store_path=tmp_path / "s")


def _proposal(seg_id):
    return {"summary_tr": "x", "actions": [
        {"type": "add_catalog_item", "item_type": "product", "ref": "n1",
         "name": "İpek Fular", "price": 850},
        {"type": "create_discount", "percent": 15, "item_refs": ["n1"],
         "starts": "2026-11-01", "ends": "2026-11-02"},
        {"type": "send_campaign", "segment_id": seg_id, "message": "Fularlar geldi!"},
    ]}


def _fake_plan(ctx, backend, max_revisions=2, lang="tr"):
    seg = backend.find_customers(inactive_days=60)
    return SimpleNamespace(turns=1, usage={}, answer=None,
                           tool_log=[{"tool": "find_customers", "args": {}, "result": seg}],
                           proposal=_proposal(seg["segment_id"]))


# --------------------------------------------------------------------------- #
# executor callback protocol
# --------------------------------------------------------------------------- #

def test_callback_order_and_counts_on_success(tmp_path):
    b = _butik(tmp_path)
    seg = b.find_customers(inactive_days=60)
    events = []
    steps = executor.execute(_proposal(seg["segment_id"]), MediaContext("x", "text"), b,
                             out_dir=tmp_path, on_step=lambda *a: events.append(a))
    assert [s["status"] for s in steps] == ["ok", "ok", "ok"]
    assert steps[1]["result"]["starts"] == "2026-11-01" and steps[1]["result"]["ends"] == "2026-11-02"
    assert [(e, i, n) for e, i, n, _ in events] == [
        ("start", 0, 3), ("ok", 1, 3), ("ok", 2, 3), ("ok", 3, 3)]
    assert events[0][3] is None
    assert [s["action"] for _, _, _, s in events[1:]] == [
        "add_catalog_item", "create_discount", "send_campaign"]
    assert events[1][3] is steps[0]                      # the announced record is the recorded one


def test_callback_order_and_counts_on_mid_plan_failure(tmp_path):
    b = _butik(tmp_path)
    seg = b.find_customers(inactive_days=60)
    prop = _proposal(seg["segment_id"])
    prop["actions"][1]["item_refs"] = ["ghost"]          # unknown item → the discount fails
    events = []
    steps = executor.execute(prop, MediaContext("x", "text"), b,
                             out_dir=tmp_path, on_step=lambda *a: events.append(a))
    assert [s["status"] for s in steps] == ["ok", "failed"]
    assert [(e, i, n) for e, i, n, _ in events] == [
        ("start", 0, 3), ("ok", 1, 3), ("failed", 2, 3), ("skipped", 3, 3)]
    assert events[3][3] == {"action": "send_campaign", "status": "skipped", "result": ""}
    assert b.social_posts == [] and b.campaigns == []   # fail-closed: nothing after the error ran


def test_raising_callback_does_not_change_execution(tmp_path, caplog):
    mk = lambda p: MockBackend("butik", today=date(2026, 10, 27), store_path=p)
    b1, b2 = mk(tmp_path / "s1"), mk(tmp_path / "s2")
    seg1, seg2 = b1.find_customers(inactive_days=60), b2.find_customers(inactive_days=60)

    def boom(*a):
        raise RuntimeError("ui down")

    with caplog.at_level("WARNING", logger="app.executor"):
        steps1 = executor.execute(_proposal(seg1["segment_id"]), MediaContext("x", "text"), b1,
                                  out_dir=tmp_path, on_step=boom)
    steps2 = executor.execute(_proposal(seg2["segment_id"]), MediaContext("x", "text"), b2,
                              out_dir=tmp_path)
    assert [(s["action"], s["status"]) for s in steps1] == [(s["action"], s["status"]) for s in steps2]
    assert len(b1.campaigns) == len(b2.campaigns) == 1 and b1.items[-1].name == b2.items[-1].name
    assert "RuntimeError" in caplog.text and "ui down" not in caplog.text  # type only, never the text


# --------------------------------------------------------------------------- #
# rendered lines, both languages, facts only
# --------------------------------------------------------------------------- #

def _rec(action, result):
    return {"action": action, "status": "ok", "result": result}


@pytest.mark.parametrize("lang,start", [("tr", "▶️ Başlıyorum — 3 adım"),
                                    ("en", "▶️ Starting — 3 steps")])
def test_start_line(lang, start):
    assert i18n.render_progress("start", 0, 3, None, lang) == start
    assert i18n.render_progress("start", 0, 1, None, "en") == "▶️ Starting — 1 step"


@pytest.mark.parametrize("lang,lines", [
    ("tr", ["✅ 1/4 Ürün eklendi: İpek Fular — 850 ₺",
            "✅ 2/4 İndirim planlandı: %15, 1–2 Kas",
            "✅ 3/4 Kampanya: 23 kişiye gitti",
            "✅ 4/4 Instagram: yarın 10:00'da yayında"]),
    ("en", ["✅ 1/4 Item added: İpek Fular — 850 ₺",
            "✅ 2/4 Discount created: 15%, Nov 1–2",
            "✅ 3/4 Campaign: sent to 23 people",
            "✅ 4/4 Instagram: going live tomorrow at 10:00"]),
])
def test_ok_lines_from_facts(lang, lines):
    r = i18n.render_progress
    assert r("ok", 1, 4, _rec("add_catalog_item", {"type": "product", "name": "İpek Fular",
                                                  "price": 850}), lang) == lines[0]
    assert r("ok", 2, 4, _rec("create_discount", {"percent": 15, "starts": "2026-11-01",
                                                 "ends": "2026-11-02"}), lang) == lines[1]
    assert r("ok", 3, 4, _rec("send_campaign", {"recipients": 23}), lang) == lines[2]
    post = _rec("publish_social_post", {"status": "scheduled", "scheduled_for": "2026-10-28T10:00",
                                        "platforms": ["instagram"]})
    assert r("ok", 4, 4, post, lang, today="2026-10-27") == lines[3]


def test_ok_lines_remaining_types_and_edges():
    r = i18n.render_progress
    assert r("ok", 1, 1, _rec("add_catalog_item", {"type": "service", "name": "Keratin",
                                                  "price": 2500}), "tr") == \
        "✅ 1/1 Hizmet eklendi: Keratin — 2,500 ₺"
    assert r("ok", 2, 3, _rec("generate_visual", {}), "en") == "✅ 2/3 Campaign visual ready"
    now = _rec("publish_social_post", {"status": "published", "scheduled_for": None,
                                       "platforms": ["instagram", "facebook"]})
    assert r("ok", 1, 1, now, "tr") == "✅ 1/1 Instagram, Facebook: paylaşıldı"
    assert r("ok", 1, 1, _rec("create_appointment", {"starts": "2026-10-31T15:00"}), "tr") == \
        "✅ 1/1 Randevu oluşturuldu: 31.10 15:00"
    assert r("ok", 1, 1, _rec("reschedule_appointment", {"starts": "2026-10-31T15:00"}),
             "en") == "✅ 1/1 Appointment moved: 31.10 15:00"
    assert r("ok", 1, 1, _rec("cancel_appointment", {}), "en") == "✅ 1/1 Appointment cancelled"
    assert r("ok", 1, 2, _rec("apply_bulk_price", {"updated": 12}), "tr") == \
        "✅ 1/2 Toplu fiyat: 12 kalem güncellendi"
    assert r("ok", 1, 2, _rec("apply_bulk_price", {"updated": 1}), "en") == \
        "✅ 1/2 Bulk prices: 1 item updated"
    assert r("ok", 1, 2, _rec("adjust_loyalty", {"points": 650, "delta": 200}), "tr") == \
        "✅ 1/2 Puan: +200 (toplam 650)"
    assert r("ok", 1, 2, _rec("adjust_loyalty", {"points": 0, "delta": -50}), "en") == \
        "✅ 1/2 Points: -50 (total 0)"
    assert r("ok", 1, 1, _rec("ads_action", {}), "tr") == "✅ 1/1 Reklam güncellendi"
    assert r("ok", 1, 1, _rec("some_future_action", {}), "tr") == "✅ 1/1 some_future_action"
    # markup, newlines and long digit runs never reach the UI
    vip = _rec("add_catalog_item", {"type": "product", "name": "*VIP*\nÇanta", "price": 10})
    assert r("ok", 1, 1, vip, "tr") == "✅ 1/1 Ürün eklendi: VIP Çanta — 10 ₺"
    assert "905321234567" not in r(
        "failed", 1, 1, {"action": "send_campaign", "status": "failed",
                         "result": "ValueError: 905321234567 nolu müşteri engelli"}, "tr")
    # unparseable dates are shown as-is, never crash
    assert r("ok", 1, 1, _rec("create_discount", {"percent": 10, "starts": "soon",
                                                 "ends": "later"}), "tr").endswith("soon–later")
    sched = _rec("publish_social_post", {"status": "scheduled", "scheduled_for": "2026-12-25T09:30",
                                         "platforms": ["instagram"]})
    assert r("ok", 1, 1, sched, "tr", today="2026-10-27") == "✅ 1/1 Instagram: 25 Ara 09:30'da yayında"
    assert r("ok", 1, 1, sched, "en", today=None) == \
        "✅ 1/1 Instagram: going live Dec 25 at 09:30"


@pytest.mark.parametrize("lang,verb", [("tr", "İndirim yapılamadı"),
                                    ("en", "Couldn't create the discount")])
def test_failed_line(lang, verb):
    step = {"action": "create_discount", "status": "failed",
            "result": "ValueError: KEDD indirim servisi kapalı"}
    assert i18n.render_progress("failed", 2, 4, step, lang) == \
        f"⛔ 2/4 {verb}: KEDD indirim servisi kapalı" + (" — kalan adımları durdurdum"
                                                       if lang == "tr"
                                                       else " — I stopped the remaining steps")
    assert "ValueError" not in i18n.render_progress("failed", 2, 4, step, lang)
    last = i18n.render_progress("failed", 4, 4, step, lang)   # nothing left to stop
    assert last.startswith(f"⛔ 4/4 {verb}: ") and "kalan" not in last and "remaining" not in last
    bare = dict(step, result="")                              # empty message, no dangling colon
    assert i18n.render_progress("failed", 4, 4, bare, lang) == f"⛔ 4/4 {verb}"
    raw = dict(step, result="plain message without a type prefix")
    assert i18n.render_progress("failed", 4, 4, raw, lang) == \
        f"⛔ 4/4 {verb}: plain message without a type prefix"


def test_skipped_and_unknown_events_are_silent():
    assert i18n.render_progress("skipped", 3, 3, {"action": "x", "status": "skipped", "result": ""},
                                "tr") == ""
    assert i18n.render_progress("bogus", 1, 1, None, "en") == ""


def test_no_customer_phone_numbers_in_any_line():
    lines = [
        i18n.render_progress("start", 0, 4, None, lang)
        for lang in ("tr", "en")
    ] + [
        i18n.render_progress("ok", i, 11, step, lang, today="2026-10-27")
        for lang in ("tr", "en")
        for i, step in enumerate([
            _rec("add_catalog_item", {"type": "product", "name": "Ayşe Yılmaz Özel", "price": 850}),
            _rec("create_discount", {"percent": 15, "starts": "2026-11-01",
                                     "ends": "2026-11-02"}),
            _rec("generate_visual", {}),
            _rec("send_campaign", {"recipients": 23}),
            _rec("publish_social_post", {"status": "scheduled",
                                         "scheduled_for": "2026-10-28T10:00",
                                         "platforms": ["instagram"]}),
            _rec("create_appointment", {"starts": "2026-10-31T15:00"}),
            _rec("reschedule_appointment", {"starts": "2026-10-31T15:00"}),
            _rec("cancel_appointment", {}),
            _rec("apply_bulk_price", {"updated": 12}),
            _rec("adjust_loyalty", {"points": 650, "delta": 200}),
            _rec("ads_action", {}),
        ], start=1)
    ]
    assert len(lines) == 24 and all(lines)
    assert not re.search(r"\d{10,}", "\n".join(lines))


# --------------------------------------------------------------------------- #
# end to end through core.handle
# --------------------------------------------------------------------------- #

def test_progress_lines_end_to_end_both_languages(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep, lang="tr": "Hallettim.")
    count = MockBackend("butik", today=date(2026, 10, 27)).find_customers(
        inactive_days=60)["count"]

    s = core.Session(sender="905321",
                     backend=_butik(tmp_path))
    core.handle(s, "text", TR_INSTRUCTION)
    notes = []
    core.handle(s, "text", "evet", notify=notes.append)
    assert notes == ["✅ Onaylandı, uyguluyorum…",
                     "▶️ Başlıyorum — 3 adım",
                     "✅ 1/3 Ürün eklendi: İpek Fular — 850 ₺",
                     "✅ 2/3 İndirim planlandı: %15, 1–2 Kas",
                     f"✅ 3/3 Kampanya: {count} kişiye gitti"]

    s = core.Session(sender="905321", backend=_butik(tmp_path))
    core.handle(s, "text", EN_INSTRUCTION)
    assert s.lang == "en"
    notes = []
    core.handle(s, "text", "yes", notify=notes.append)
    assert notes == ["✅ Approved, applying it…",
                     "▶️ Starting — 3 steps",
                     "✅ 1/3 Item added: İpek Fular — 850 ₺",
                     "✅ 2/3 Discount created: 15%, Nov 1–2",
                     f"✅ 3/3 Campaign: sent to {count} people"]


def test_failed_step_announces_and_skipped_tail_stays_silent(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)

    def _bad_plan(ctx, backend, max_revisions=2, lang="tr"):
        seg = backend.find_customers(inactive_days=60)
        prop = _proposal(seg["segment_id"])
        prop["actions"][1]["item_refs"] = ["ghost"]
        return SimpleNamespace(turns=1, usage={}, answer=None,
                               tool_log=[{"tool": "find_customers", "args": {}, "result": seg}],
                               proposal=prop)

    monkeypatch.setattr(agent, "plan", _bad_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep, lang="tr": "Hallettim.")
    s = core.Session(sender="905321", backend=_butik(tmp_path))
    core.handle(s, "text", TR_INSTRUCTION)
    notes = []
    summary = core.handle(s, "text", "evet", notify=notes.append)[0].text
    assert notes[0] == "✅ Onaylandı, uyguluyorum…" and notes[1] == "▶️ Başlıyorum — 3 adım"
    assert notes[2] == "✅ 1/3 Ürün eklendi: İpek Fular — 850 ₺"
    assert notes[3].startswith("⛔ 2/3 İndirim yapılamadı: ") and "ValueError" not in notes[3]
    assert notes[3].endswith("— kalan adımları durdurdum") and len(notes) == 4
    assert "başarısız" in summary and s.backend.campaigns == []


def _pending_kedd_session(monkeypatch, tmp_path, business, backend, lang="tr"):
    monkeypatch.setattr(report, "build_report", lambda steps, backend: {"steps": steps})
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep, lang="tr": "Done.")
    monkeypatch.setattr(executor, "execute",
                        lambda *a, **k: [{"action": "send_campaign", "status": "ok",
                                          "result": {"recipients": 1}}])
    s = core.Session(sender="905321", business=business, backend=backend, lang=lang)
    s.pending = {"proposal": {"summary_tr": "x", "actions": []}, "ctx": MediaContext("x", "text"),
                 "seg_counts": {}, "run_dir": str(tmp_path), "at": time.time(),
                 "shown_at": time.time()}
    return s


def test_kedd_panel_note_only_in_kedd_mode(monkeypatch, tmp_path):
    kedd = KeddBackend("https://kedd.example", "s3cret", "+905321")
    s = _pending_kedd_session(monkeypatch, tmp_path, "kedd", kedd)
    assert core.handle(s, "text", "evet")[0].text == "Done.\n🔔 Hepsi KEDD panelinde bildirimlerde."

    s = _pending_kedd_session(monkeypatch, tmp_path, "kedd", kedd, lang="en")
    assert core.handle(s, "text", "yes")[0].text == \
        "Done.\n🔔 It's all in the KEDD panel notifications."

    s = _pending_kedd_session(monkeypatch, tmp_path, "butik", _butik(tmp_path))
    assert core.handle(s, "text", "evet")[0].text == "Done."


# --------------------------------------------------------------------------- #
# web: GET /api/progress
# --------------------------------------------------------------------------- #

def test_progress_endpoint_returns_rendered_lines_session_scoped(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep, lang="tr": "Hallettim.")
    app_obj = webchan.create_app()
    c = TestClient(app_obj)
    c.get("/")
    assert c.get("/api/progress").json() == {"lines": [], "next": 0}

    body = c.post("/api/message", data={"text": TR_INSTRUCTION}).json()
    assert body["status"] == ["⏳ Bakıyorum…"]          # the final shape is unchanged
    first = c.get("/api/progress").json()
    assert first == {"lines": [{"seq": 1, "text": "⏳ Bakıyorum…"}], "next": 1}

    body = c.post("/api/message", data={"text": "evet"}).json()
    live = c.get("/api/progress?since=1").json()
    assert [line["text"] for line in live["lines"]] == body["status"]
    assert live["lines"][0]["text"] == "✅ Onaylandı, uyguluyorum…"
    assert "▶️ Başlıyorum — 3 adım" in [line["text"] for line in live["lines"]]
    assert all(set(line) == {"seq", "text"} for line in live["lines"])
    assert [line["seq"] for line in live["lines"]] == list(range(2, live["next"] + 1))
    assert not re.search(r"\d{10,}", "\n".join(line["text"] for line in live["lines"]))
    assert c.get("/api/progress?since=abc").json()["next"] == live["next"]  # bad cursor → all

    other = TestClient(app_obj)                       # another session sees nothing of this one
    other.get("/")
    assert other.get("/api/progress").json() == {"lines": [], "next": 0}

    anon = TestClient(app_obj)                        # no cookie at all → empty, not an error
    assert anon.get("/api/progress").json() == {"lines": [], "next": 0}


def test_progress_polling_marks_for_app_js():
    src = (webchan.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'fetch("/api/progress?since=" + progressSeq)' in src
    assert "setInterval(pollProgress, 700)" in src
