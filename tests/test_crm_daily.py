"""Task 26: CRM + daily work — customers, leads, tasks, insights.

Validation (ids from the same run, contact rules for notes/tags, due dates, the
no-campaign-to-new-customer rule), card + progress lines in TR/EN, executor
ref-chaining, KeddBackend request bodies + idempotency keys, 409 exists handling
in Turkish, demo-key rules, and mock offline behaviour. Shape parity lives in
tests/test_kedd_shapes.py; everything here is offline (stubbed HTTP).
"""

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import actions, approval, executor
from app.actions import validate_proposal
from app.agent import TOOLS
from app.approval import card_facts, render_card
from app.pipeline import MediaContext
from app.tools.backend import KeddBackend, MockBackend
from app import i18n

FIX = Path(__file__).parent / "fixtures" / "kedd"
BASE = "https://kedd.example"
DAY0 = date(2026, 10, 27)


def _load(name: str):
    return json.loads((FIX / f"{name}.json").read_text())


class _Stub:
    """Serve one payload per (method, gateway path); record traffic. No network."""

    def __init__(self, routes: dict[tuple[str, str], object]):
        self.routes = routes
        self.calls: list[tuple] = []

    def _call(self, method, url, headers=None, data=None):
        self.calls.append((method, url, headers, json.loads(data) if data else None))
        route = self.routes[(method, url[len(BASE):].split("?")[0])]
        if isinstance(route, Exception):
            raise route
        status, body = route if isinstance(route, tuple) else (200, route)
        return SimpleNamespace(json=lambda: body, status_code=status)

    def get(self, url, headers=None, timeout=None):
        return self._call("GET", url, headers)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._call("POST", url, headers, data)


_CRM_PATHS = {
    "crm_leads": ("/agent/leads", "GET"), "crm_tasks": ("/agent/tasks", "GET"),
    "crm_insights": ("/agent/insights", "GET"),
    "crm_customer_create": ("/agent/customers", "POST"),
    "crm_customer_update": ("/agent/customers/update", "POST"),
    "crm_lead_create": ("/agent/leads", "POST"),
    "crm_lead_stage": ("/agent/leads/stage", "POST"),
    "crm_lead_note": ("/agent/leads/note", "POST"),
    "crm_task_create": ("/agent/tasks", "POST"),
    "crm_task_complete": ("/agent/tasks/complete", "POST"),
}


def _kb(*names: str, key_id: str = "main", routes_extra: dict | None = None) -> KeddBackend:
    routes = {(_CRM_PATHS[n][1], _CRM_PATHS[n][0]): _load(n) for n in names}
    routes.update(routes_extra or {})
    kb = KeddBackend(BASE, "s3cret", "+905321", key_id=key_id)
    kb._s = _Stub(routes)
    return kb


def _valid(**kw) -> dict:
    """A valid CRM plan; callers override actions. known_* must pin every id used."""
    prop = {"summary_tr": "müşteri ve görev işleri yaptım, mesaj göndermedim", **kw}
    for a in prop.get("actions") or []:
        a.setdefault("label", f"{a.get('type')} işi")  # every action needs a card label
    return prop


def _v(prop, customers=frozenset(), leads=frozenset(), tasks=frozenset()):
    return validate_proposal(prop, set(), {}, set(), set(), customers, {}, set(), {},
                             requires_template=False, known_leads=leads, known_tasks=tasks)


def _future(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------- #
# validation: ids, refs, limits, due dates, consent rule
# --------------------------------------------------------------------------- #

def test_ids_must_come_from_this_run():
    prop = _valid(actions=[
        {"type": "update_customer", "customer_id": "c-nope", "add_tags": ["vip"]},
        {"type": "set_lead_stage", "lead_id": "lead-nope", "stage": "won"},
        {"type": "add_lead_note", "lead_id": "lead-nope", "note": "aradı"},
        {"type": "complete_task", "task_id": "task-nope"}])
    errs = _v(prop)
    assert sum("must come from" in e for e in errs) == 4
    assert any("find_customer" in e for e in errs)
    assert any("find_leads" in e for e in errs)
    assert any("list_tasks" in e for e in errs)
    ok = _valid(actions=[
        {"type": "update_customer", "customer_id": "c1", "add_tags": ["vip"]},
        {"type": "set_lead_stage", "lead_id": "l1", "stage": "won"},
        {"type": "add_lead_note", "lead_id": "l1", "note": "aradı"},
        {"type": "complete_task", "task_id": "t1"}])
    assert _v(ok, {"c1"}, {"l1"}, {"t1"}) == []


def test_refs_chain_within_a_plan_and_cannot_be_reused_or_mixed():
    chain = _valid(actions=[
        {"type": "create_customer", "name": "Ada Kafe", "ref": "c1"},
        {"type": "update_customer", "customer_id": "c1", "add_tags": ["vip"]},
        {"type": "create_lead", "name": "Lavanta Spa", "ref": "l1"},
        {"type": "set_lead_stage", "lead_id": "l1", "stage": "contacted"},
        {"type": "create_task", "title": "Ara", "due_date": _future(2), "customer_id": "c1",
         "lead_id": "l1", "ref": "t1"},
        {"type": "complete_task", "task_id": "t1"}])
    assert _v(chain) == []
    # A ref used before it is created, or as the wrong kind, is rejected.
    assert any("must come from" in e for e in _v(_valid(actions=[
        {"type": "complete_task", "task_id": "t9"},
        {"type": "create_task", "title": "Ara", "due_date": _future(2), "ref": "t9"}])))
    assert any("must come from" in e for e in _v(_valid(actions=[
        {"type": "create_customer", "name": "Ada Kafe", "ref": "x1"},
        {"type": "set_lead_stage", "lead_id": "x1", "stage": "won"}])))
    # A ref must not shadow a tool-seen id or repeat.
    assert any("already used" in e for e in _v(_valid(actions=[
        {"type": "create_lead", "name": "Lavanta Spa", "ref": "l1"}]), leads={"l1"}))
    assert any("already used" in e for e in _v(_valid(actions=[
        {"type": "create_lead", "name": "Bir", "ref": "l1"},
        {"type": "create_lead", "name": "İki", "ref": "l1"}])))


def test_create_customer_cannot_share_a_plan_with_send_campaign():
    prop = {"summary_tr": "yeni müşteriye hoş geldin mesajı gönderdim", "actions": [
        {"type": "create_customer", "name": "Ada Kafe"},
        {"type": "send_campaign", "segment_id": "seg1", "message": "Hoş geldin!"}]}
    assert any("no marketing consent" in e for e in _v(prop))
    # Other CRM writes combine with campaigns freely.
    ok = {"summary_tr": "görev açıldı ve segmente mesaj gönderildi", "actions": [
        {"type": "create_task", "title": "Ara", "due_date": _future(2), "label": "Görev aç"},
        {"type": "send_campaign", "segment_id": "seg1", "message": "Hoş geldin!"}]}
    assert validate_proposal(ok, set(), {}, {"seg1"}, requires_template=False) == []


@pytest.mark.parametrize("bad, kind", [
    ("bak https://ornek.com/x", "URL"), ("yaz info@dukkan.com", "e-mail"),
    ("ara 0532 123 45 67", "phone"), ("TR330006100519786457841326", "IBAN")])
def test_contacts_rejected_in_notes_and_tags(bad, kind):
    prop = _valid(actions=[
        {"type": "create_customer", "name": "Ada Kafe", "tags": ["vip", bad]},
        {"type": "create_lead", "name": "Lavanta Spa", "note": bad},
        {"type": "add_lead_note", "lead_id": "l1", "note": bad}])
    errs = _v(prop, leads={"l1"})
    assert sum(kind in e for e in errs) == 3, errs


def test_name_note_tag_title_limits():
    prop = _valid(actions=[
        {"type": "create_customer", "name": "A"},                       # too short
        {"type": "create_lead", "name": "N" * 81, "note": "ok"},        # name too long
        {"type": "update_customer", "customer_id": "c1", "note": "N" * 501},
        {"type": "create_customer", "name": "Ada Kafe", "tags": [f"t{i}" for i in range(11)]},
        {"type": "create_lead", "name": "Lavanta", "tags": ["T" * 31]},
        {"type": "create_task", "title": "T" * 121, "due_date": _future(2)},
        {"type": "create_lead", "name": "Lavanta", "estimated_value": -5},
        {"type": "set_lead_stage", "lead_id": "l1", "stage": "maybe"},
        {"type": "update_customer", "customer_id": "c1", "name": "B"},
        {"type": "add_lead_note", "lead_id": "l1"}])                     # note required
    errs = _v(prop, {"c1"}, {"l1"})
    assert len(errs) == 10, errs
    assert any("estimated_value" in e for e in errs)
    assert any("stage must be" in e for e in errs)


def test_due_date_rules():
    past = _valid(actions=[{"type": "create_task", "title": "Ara",
                             "due_date": (date.today() - timedelta(days=1)).isoformat()}])
    assert any("in the past" in e for e in _v(past))
    far = _valid(actions=[{"type": "create_task", "title": "Ara",
                           "due_date": (date.today() + timedelta(days=366)).isoformat()}])
    assert any("more than a year" in e for e in _v(far))
    bad = _valid(actions=[{"type": "create_task", "title": "Ara", "due_date": "yarın öğlen"}])
    assert any("ISO date or datetime" in e for e in _v(bad))
    ok = _valid(actions=[
        {"type": "create_task", "title": "Ara", "due_date": _future(0)},
        {"type": "create_task", "title": "Git", "due_date": f"{_future(1)}T10:00"}])
    assert _v(ok) == []


def test_create_task_links_must_come_from_this_run():
    prop = _valid(actions=[{"type": "create_task", "title": "Ara", "due_date": _future(2),
                             "customer_id": "c-nope", "lead_id": "l-nope"}])
    assert sum("must come from" in e for e in _v(prop)) == 2
    ok = _valid(actions=[{"type": "create_task", "title": "Ara", "due_date": _future(2),
                           "customer_id": "c1"}])
    assert _v(ok, {"c1"}) == []


# --------------------------------------------------------------------------- #
# cards + progress in both owner languages
# --------------------------------------------------------------------------- #

def _ctx() -> MediaContext:
    return MediaContext(products=[], instruction="", instruction_source="none")


def _card(actions_list, lang, facts=None) -> str:
    return render_card({"summary_tr": "özet", "actions": actions_list}, _ctx(), {},
                       card_facts(facts or []), lang)


_CRM_ACTIONS = [
    {"type": "create_customer", "name": "Ada Kafe", "tags": ["kafe"], "ref": "c1"},
    {"type": "update_customer", "customer_id": "c1", "add_tags": ["vip"], "note": "Aradı"},
    {"type": "create_lead", "name": "Lavanta Spa", "estimated_value": 3500, "ref": "l1"},
    {"type": "set_lead_stage", "lead_id": "l1", "stage": "won"},
    {"type": "add_lead_note", "lead_id": "l1", "note": "Sözleşme imzalandı, kurulum haftaya"},
    {"type": "create_task", "title": "Ada Kafe'yi ara", "due_date": "2026-10-30T10:00"},
    {"type": "complete_task", "task_id": "t1"},
]
_TASK_FACTS = [{"tool": "list_tasks", "result": {"tasks": [
    {"id": "t1", "title": "Fiyat listesini gönder", "due_date": "2026-10-28"}]}}]


def test_card_lines_tr():
    card = _card(_CRM_ACTIONS, "tr", _TASK_FACTS)
    assert "👤 Yeni müşteri: Ada Kafe (etiket: kafe)" in card
    assert "🏷️ Ada Kafe: +etiket vip, not eklendi" in card
    assert "🧲 Yeni lead: Lavanta Spa — tahmini 3,500 ₺" in card
    assert "📈 Lavanta Spa: yeni → kazanıldı" in card
    assert "📝 Not: Sözleşme imzalandı, kurulum haftaya" in card
    assert "⏰ Görev: Ada Kafe'yi ara — Cuma 10:00" in card
    assert "✅ Görev tamamlandı: Fiyat listesini gönder" in card
    assert "🔕" in card and "onayı olmadığı için" in card  # consent note


def test_card_lines_en():
    card = _card(_CRM_ACTIONS, "en", _TASK_FACTS)
    assert "👤 New customer: Ada Kafe (tags: kafe)" in card
    assert "🏷️ Ada Kafe: +tag vip, note added" in card
    assert "🧲 New lead: Lavanta Spa — est. 3,500 ₺" in card
    assert "📈 Lavanta Spa: new → won" in card
    assert "📝 Note: Sözleşme imzalandı, kurulum haftaya" in card
    assert "⏰ Task: Ada Kafe'yi ara — Friday 10:00" in card
    assert "✅ Task completed: Fiyat listesini gönder" in card
    assert "no marketing consent" in card


def test_card_note_preview_and_date_only_due():
    long_note = "N" * 100
    card = _card([{"type": "add_lead_note", "lead_id": "l1", "note": long_note},
                  {"type": "create_task", "title": "Ara", "due_date": "2026-11-02"}], "tr")
    assert f"📝 Not: {'N' * 79}…" in card  # first 80 chars only
    assert "⏰ Görev: Ara — 2 Kas" in card


def test_no_consent_note_without_create_customer():
    card = _card([{"type": "create_lead", "name": "Lavanta Spa"}], "tr")
    assert "🔕" not in card


def _progress_steps():
    return [
        {"action": "create_customer", "result": {"ok": True, "name": "Ada Kafe", "tags": ["kafe"]}},
        {"action": "update_customer", "result": {"ok": True, "name": "Ada Kafe",
                                                "added_tags": ["vip"], "note_added": True}},
        {"action": "create_lead", "result": {"ok": True, "name": "Lavanta Spa", "value": 3500}},
        {"action": "set_lead_stage", "result": {"ok": True, "prev_stage": "proposal",
                                               "stage": "won"}},
        {"action": "add_lead_note", "result": {"ok": True, "note": "Sözleşme imzalandı"}},
        {"action": "create_task", "result": {"ok": True, "title": "Ada Kafe'yi ara",
                                            "due_date": "2026-10-30T10:00"}},
        {"action": "complete_task", "result": {"ok": True, "title": "Fiyat listesi"}},
    ]


def test_progress_lines_tr_and_en():
    tr = [i18n.render_progress("ok", i + 1, 7, s, "tr") for i, s in enumerate(_progress_steps())]
    assert tr[0] == "✅ 1/7 Yeni müşteri: Ada Kafe (etiket: kafe)"
    assert tr[1] == "✅ 2/7 Ada Kafe: +etiket vip, not eklendi"
    assert tr[2] == "✅ 3/7 Yeni lead: Lavanta Spa — tahmini 3,500 ₺"
    assert tr[3] == "✅ 4/7 Lead: teklif → kazanıldı"
    assert tr[4] == "✅ 5/7 Not: Sözleşme imzalandı"
    assert tr[5] == "✅ 6/7 Görev: Ada Kafe'yi ara — Cuma 10:00"
    assert tr[6] == "✅ 7/7 Görev tamamlandı"
    en = [i18n.render_progress("ok", i + 1, 7, s, "en") for i, s in enumerate(_progress_steps())]
    assert en[3] == "✅ 4/7 Lead: proposal → won"
    assert en[5] == "✅ 6/7 Task: Ada Kafe'yi ara — Friday 10:00"
    assert all(line for line in tr + en)  # every new action announces itself


def test_format_task_due():
    assert i18n.format_task_due("2026-10-30T10:00", "tr") == "Cuma 10:00"
    assert i18n.format_task_due("2026-10-30T10:00", "en") == "Friday 10:00"
    assert i18n.format_task_due("2026-11-02", "tr") == "2 Kas"
    assert i18n.format_task_due("2026-11-02", "en") == "Nov 2"
    assert i18n.format_task_due("yarın öğlen", "tr") == "yarın öğlen"  # shown as-is
    assert i18n.format_task_due(None, "tr") == "?"


# --------------------------------------------------------------------------- #
# gateway request bodies + idempotency keys (all offline, stubbed HTTP)
# --------------------------------------------------------------------------- #

def test_request_bodies_and_idempotency_keys():
    kb = _kb("crm_customer_create", "crm_customer_update", "crm_lead_create", "crm_lead_stage",
             "crm_lead_note", "crm_task_create", "crm_task_complete")
    kb.create_customer("Ada Kafe", phone="0532", email="a@b.co", tags=["kafe"], note="n",
                       idempotency_key="k1")
    kb.update_customer("c-9", name="Ada", add_tags=["vip"], remove_tags=["eski"], note="m",
                       idempotency_key="k2")
    kb.create_lead("Lavanta", estimated_value=3500, tags=["spa"], idempotency_key="k3")
    kb.set_lead_stage("lead-1", "won", idempotency_key="k4")
    kb.add_lead_note("lead-1", "aradı", idempotency_key="k5")
    kb.create_task("Ara", "2026-10-30T10:00", customer_id="c-9", idempotency_key="k6")
    kb.complete_task("task-1", idempotency_key="k7")
    posts = [c for c in kb._s.calls if c[0] == "POST"]
    assert [(p[1][len(BASE):], p[3]) for p in posts] == [
        ("/agent/customers", {"name": "Ada Kafe", "phone": "0532", "email": "a@b.co",
                              "tags": ["kafe"], "note": "n"}),
        ("/agent/customers/update", {"customer_id": "c-9", "name": "Ada", "add_tags": ["vip"],
                                    "remove_tags": ["eski"], "note": "m"}),
        ("/agent/leads", {"name": "Lavanta", "estimatedValue": 3500, "tags": ["spa"]}),
        ("/agent/leads/stage", {"lead_id": "lead-1", "stage": "won"}),
        ("/agent/leads/note", {"lead_id": "lead-1", "note": "aradı"}),
        ("/agent/tasks", {"title": "Ara", "due_date": "2026-10-30T10:00", "customer_id": "c-9"}),
        ("/agent/tasks/complete", {"task_id": "task-1"}),
    ]
    assert [p[2]["Idempotency-Key"] for p in posts] == [f"k{i}" for i in range(1, 8)]
    # Absent optionals are dropped from the body, never sent as null.
    assert "lead_id" not in posts[5][3] and "email" not in posts[2][3]


def test_read_query_params():
    kb = _kb("crm_leads", "crm_tasks", "crm_insights")
    kb.find_leads(stage="proposal", name="Lavanta")
    kb.list_tasks(status="overdue")
    kb.get_insights(30)
    kb.get_insights(999)  # clamped to the gateway's 1–90 window
    gets = [c[1][len(BASE):] for c in kb._s.calls]
    assert gets[0].startswith("/agent/leads?") and "stage=proposal" in gets[0]
    assert "name=Lavanta" in gets[0]
    assert gets[1] == "/agent/tasks?status=overdue"
    assert gets[2] == "/agent/insights?days=30"
    assert gets[3] == "/agent/insights?days=90"


def test_409_exists_surfaces_turkish_with_the_record():
    kb = _kb(routes_extra={
        ("POST", "/agent/customers"): (409, {"error": "customer_exists", "customer_id": "c-9"}),
        ("POST", "/agent/leads"): (409, {"error": "lead_exists", "lead_id": "lead-1",
                                         "message": "Bu lead zaten kayıtlı"})})
    with pytest.raises(ValueError, match="zaten kayıtlı.*Ada Kafe.*c-9"):
        kb.create_customer("Ada Kafe")
    # A gateway Turkish message is shown verbatim, with the record offered.
    with pytest.raises(ValueError, match="Bu lead zaten kayıtlı.*lead-1"):
        kb.create_lead("Lavanta Spa")
    other = _kb(routes_extra={
        ("POST", "/agent/customers"): (409, {"error": "unknown_outcome"})})
    with pytest.raises(ValueError, match="belirsiz"):
        other.create_customer("Ada Kafe")


def test_mock_duplicates_speak_turkish_and_name_the_record():
    m = MockBackend("salon", today=DAY0)
    name = m.customers[0].name
    with pytest.raises(ValueError, match=f"zaten kayıtlı.*{name}.*{m.customers[0].id}"):
        m.create_customer(name)
    m.create_lead("Lavanta Test")
    with pytest.raises(ValueError, match="zaten kayıtlı.*Lavanta Test"):
        m.create_lead("lavanta test")  # case-insensitive, like the gateway
    assert "devam edebilirsin" in str(pytest.raises(ValueError, m.create_lead,
                                                    "Lavanta Test").value)


def test_demo_key_creates_a_customer_without_a_phone():
    kb = _kb("crm_customer_create", key_id="demo")
    assert kb.create_customer("Ada Kafe") == {"id": "c-9", "name": "Ada Kafe"}
    with pytest.raises(ValueError, match="telefonsuz"):
        kb.create_customer("Ada Kafe", phone="05321234567")
    assert len(kb._s.calls) == 1  # the refusal happens before any network call


# --------------------------------------------------------------------------- #
# mock offline behaviour: seeds, filters, insights
# --------------------------------------------------------------------------- #

def test_mock_seeds_stay_green_and_filtered():
    m = MockBackend("salon", today=DAY0)
    assert m.SCHEMA == 8
    assert m.find_leads()["truncated"] is False
    assert {l["stage"] for l in m.find_leads(stage="new")["leads"]} == {"new"}
    first = m.leads[0].name.split()[0]
    assert all(first.lower() in l["name"].lower() for l in m.find_leads(name=first)["leads"])
    assert m.list_tasks(status="open")["tasks"]
    assert all(t["overdue"] for t in m.list_tasks(status="overdue")["tasks"])
    with pytest.raises(ValueError, match="durum anlaşılmadı"):
        m.list_tasks(status="someday")


def test_mock_insights_cover_the_brief_line():
    m = MockBackend("salon", today=DAY0)
    ins = m.get_insights(7)
    assert ins["days"] == 7 and ins["revenue"]["diff"] == round(
        ins["revenue"]["paid_total"] - ins["revenue"]["prev_paid_total"], 2)
    assert set(ins["funnel"]) == {"new", "contacted", "qualified", "proposal", "won", "lost"}
    assert sum(ins["funnel"].values()) == len(m.leads)
    open_tasks = len(m.list_tasks(status="open")["tasks"])
    overdue = len(m.list_tasks(status="overdue")["tasks"])
    assert open_tasks >= overdue  # the morning-brief counts come from the same seeds
    assert m.get_insights(0)["days"] == 1 and m.get_insights(500)["days"] == 90


def test_mock_task_links_must_exist():
    m = MockBackend("salon", today=DAY0)
    with pytest.raises(ValueError, match="Müşteri bulunamadı"):
        m.create_task("Ara", "2026-10-30", customer_id="c-nope")
    with pytest.raises(ValueError, match="Lead bulunamadı"):
        m.create_task("Ara", "2026-10-30", lead_id="lead-nope")
    cid = m.create_customer("Ada Test")["id"]
    tid = m.create_task("Ara", "2026-10-30", customer_id=cid)["id"]
    assert m.complete_task(tid) == {"id": tid, "status": "completed"}


# --------------------------------------------------------------------------- #
# executor + agent wiring
# --------------------------------------------------------------------------- #

def test_executor_chains_refs_and_emits_progress():
    m = MockBackend("salon", today=DAY0)
    events: list[tuple] = []
    steps = executor.execute(_valid(actions=[
        {"type": "create_customer", "name": "Zeynep Yeni", "ref": "c1"},
        {"type": "update_customer", "customer_id": "c1", "add_tags": ["vip"]},
        {"type": "create_lead", "name": "Kıraathane", "ref": "l1"},
        {"type": "set_lead_stage", "lead_id": "l1", "stage": "qualified"},
        {"type": "add_lead_note", "lead_id": "l1", "note": "aradı"},
        {"type": "create_task", "title": "Teklifi gönder", "due_date": _future(3),
         "customer_id": "c1", "lead_id": "l1"},
        {"type": "complete_task", "task_id": m.tasks[0].id},
    ]), _ctx(), m, on_step=lambda e, i, n, s: events.append((e, i, s)))
    assert all(s["status"] == "ok" for s in steps)
    assert steps[3]["result"]["prev_stage"] == "new"  # a fresh lead starts at "new"
    assert [e[0] for e in events] == ["start"] + ["ok"] * 7
    lines = [i18n.render_progress(e, i, 7, s, "tr") for e, i, s in events[1:]]
    assert lines[0].startswith("✅ 1/7 Yeni müşteri: Zeynep Yeni")
    assert "nitelikli" in lines[3]
    assert all(lines)


def test_executor_failure_stops_and_speaks_turkish():
    m = MockBackend("salon", today=DAY0)
    m.create_lead("Lavanta Test")
    events: list[tuple] = []
    steps = executor.execute(_valid(actions=[
        {"type": "create_lead", "name": "Lavanta Test"},
        {"type": "create_task", "title": "Asla çalışmaz", "due_date": _future(1)},
    ]), _ctx(), m, on_step=lambda e, i, n, s: events.append((e, i, s)))
    assert [s["status"] for s in steps] == ["failed"]  # stops, nothing after runs
    assert "zaten kayıtlı" in steps[0]["result"]
    assert all(t.title != "Asla çalışmaz" for t in m.tasks)
    assert events[1][0] == "failed"
    line = i18n.render_progress("failed", 1, 2, steps[0], "tr")
    assert "zaten kayıtlı" in line


def test_agent_tools_and_prompt_cover_crm():
    by_name = {t["name"]: t for t in TOOLS}
    assert {"find_leads", "list_tasks", "get_insights"} <= set(by_name)
    assert by_name["find_leads"]["parameters"]["properties"]["stage"]["enum"] == list(
        actions.LEAD_STAGES)
    assert "morning brief" in by_name["get_insights"]["description"]
    from app.agent import SYSTEM_PROMPT
    assert "get_insights" in SYSTEM_PROMPT and "morning brief" in SYSTEM_PROMPT
    assert "marketing consent" in SYSTEM_PROMPT


def test_coverage_lists_crm_tasks_insights_as_live():
    cov_path = Path(__file__).parent.parent / "app" / "static" / "coverage.json"
    cov = json.loads(cov_path.read_text())
    live = {c["area"] for c in cov if c["status"] == "live"}
    assert {"crm", "tasks", "insights"} <= live
    what = " ".join(c["what_en"] for c in cov)
    assert "Customers & leads (CRM)" in what and "Tasks & reminders" in what
    assert "Weekly insights" in what
