"""Task 27: complete + revenue, quote drafts, chatbot knowledge, company names.

Validation (appointment ids from the run, amount default from catalog facts, quote
floor, chatbot price/contact rules), cards + progress in TR/EN, company display
everywhere a customer/lead is named, KeddBackend request bodies + idempotency keys,
gateway errors in Turkish, and mock offline behaviour. Shape parity lives in
tests/test_kedd_shapes.py; everything here is offline (stubbed HTTP).
"""

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import executor
from app.actions import validate_proposal
from app.agent import TOOLS, AgentRun, _run_tool, SYSTEM_PROMPT
from app.approval import card_facts, render_card
from app.pipeline import MediaContext
from app.tools.backend import KeddBackend, MockBackend
from app import i18n

FIX = Path(__file__).parent / "fixtures" / "kedd"
BASE = "https://kedd.example"
DAY0 = date(2026, 10, 27)
CATALOG = {"i1": {"price": 3500.0, "name": "KEDD Büyüme", "type": "product"},
           "i2": {"price": 2500.0, "name": "Kurulum", "type": "service"}}


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


_PATHS = {
    "appt_complete": ("/agent/appointments/complete", "POST"),
    "quote_create": ("/agent/quotes", "POST"),
    "knowledge_create": ("/agent/chatbot/knowledge", "POST"),
    "knowledge_search": ("/agent/chatbot/knowledge", "GET"),
}


def _kb(*names: str, routes_extra: dict | None = None) -> KeddBackend:
    routes = {(_PATHS[n][1], _PATHS[n][0]): _load(n) for n in names}
    routes.update(routes_extra or {})
    kb = KeddBackend(BASE, "s3cret", "+905321")
    kb._s = _Stub(routes)
    return kb


def _valid(*acts: dict) -> dict:
    """A CRM-free plan; every action gets the label the card requires."""
    prop = {"summary_tr": "randevu ve teklif işleri yaptım", "actions": list(acts)}
    for a in prop["actions"]:
        a.setdefault("label", f"{a.get('type')} işi")
    return prop


def _v(prop, catalog=None, appts=frozenset(), customers=frozenset(), leads=frozenset(),
       services=None):
    return validate_proposal(prop, set(), catalog if catalog is not None else CATALOG, set(),
                             appts, customers, {}, set(), {}, requires_template=False,
                             known_leads=leads, known_tasks=set(),
                             appointment_services=services)


def _ctx() -> MediaContext:
    return MediaContext(products=[], instruction="", instruction_source="none")


# --------------------------------------------------------------------------- #
# validation: complete_appointment
# --------------------------------------------------------------------------- #

def test_complete_needs_a_run_seen_appointment():
    prop = _valid({"type": "complete_appointment", "appointment_id": "a-nope", "amount": 100})
    errs = _v(prop, appts={"a1"})
    assert any("find_appointments" in e and "get_schedule" in e for e in errs)
    ok = _valid({"type": "complete_appointment", "appointment_id": "a1", "amount": 100,
                 "method": "cash"})
    assert _v(ok, appts={"a1"}) == []


def test_amount_defaults_to_the_service_catalog_price():
    prop = _valid({"type": "complete_appointment", "appointment_id": "a1"})
    assert _v(prop, appts={"a1"}, services={"a1": "Kurulum"}) == []
    assert prop["actions"][0]["amount"] == 2500.0
    # An explicit amount is respected, never overwritten.
    prop = _valid({"type": "complete_appointment", "appointment_id": "a1", "amount": 2000})
    assert _v(prop, appts={"a1"}, services={"a1": "Kurulum"}) == []
    assert prop["actions"][0]["amount"] == 2000


def test_amount_required_when_the_price_is_not_unique():
    assert any("amount is required" in e
               for e in _v(_valid({"type": "complete_appointment", "appointment_id": "a1"}),
                            appts={"a1"}, services={"a1": "Bilinmeyen Hizmet"}))
    dup = dict(CATALOG, i3={"price": 999.0, "name": "Kurulum", "type": "service"})
    assert any("amount is required" in e
               for e in _v(_valid({"type": "complete_appointment", "appointment_id": "a1"}),
                            catalog=dup, appts={"a1"}, services={"a1": "Kurulum"}))


@pytest.mark.parametrize("amount", [0, -5, 100001, True, "çok"])
def test_amount_bounds(amount):
    prop = _valid({"type": "complete_appointment", "appointment_id": "a1", "amount": amount})
    assert any("amount must be" in e for e in _v(prop, appts={"a1"}))


def test_complete_method_and_note_rules():
    prop = _valid({"type": "complete_appointment", "appointment_id": "a1", "amount": 100,
                   "method": "çek"})
    assert any("method must be" in e for e in _v(prop, appts={"a1"}))
    prop = _valid({"type": "complete_appointment", "appointment_id": "a1", "amount": 100,
                   "note": "ara 0532 123 45 67"})
    assert any("phone" in e for e in _v(prop, appts={"a1"}))
    prop = _valid({"type": "complete_appointment", "appointment_id": "a1", "amount": 100,
                   "method": "transfer", "note": "Elden aldı."})
    assert _v(prop, appts={"a1"}) == []


# --------------------------------------------------------------------------- #
# validation: create_quote_draft
# --------------------------------------------------------------------------- #

def _quote(**kw) -> dict:
    base = {"type": "create_quote_draft", "customer_id": "c1",
            "items": [{"catalog_item_id": "i1", "qty": 1}]}
    base.update(kw)
    return base


def test_quote_needs_exactly_one_known_party():
    assert any("exactly one" in e for e in _v(_valid(_quote(customer_id="c1", lead_id="l1"))))
    assert any("exactly one" in e
               for e in _v(_valid(_quote(customer_id=None, lead_id=None))))
    assert any("find_customer" in e
               for e in _v(_valid(_quote(customer_id="c-nope")), customers={"c1"}))
    assert any("find_leads" in e
               for e in _v(_valid(_quote(customer_id=None, lead_id="l-nope")), leads={"l1"}))
    assert _v(_valid(_quote()), customers={"c1"}) == []
    assert _v(_valid(_quote(customer_id=None, lead_id="l1")), leads={"l1"}) == []


def test_quote_rows_validated():
    assert any("non-empty list" in e
               for e in _v(_valid(_quote(items=[])), customers={"c1"}))
    assert any("unknown catalog_item_id" in e
               for e in _v(_valid(_quote(items=[{"catalog_item_id": "i-nope", "qty": 1}])),
                            customers={"c1"}))
    for qty in (0, 101, "iki", True, 1.5):
        prop = _valid(_quote(items=[{"catalog_item_id": "i1", "qty": qty}]))
        assert any("qty must be" in e for e in _v(prop, customers={"c1"})), qty
    prop = _valid(_quote(items=[{"catalog_item_id": "i1", "qty": "2"}]))
    assert _v(prop, customers={"c1"}) == []  # sloppy numbers are coerced, like elsewhere
    assert prop["actions"][0]["items"][0]["qty"] == 2
    prop = _valid(_quote(items=[{"catalog_item_id": "i1", "qty": 1, "unit_price": -1}]))
    assert any("unit_price must be 0 or more" in e for e in _v(prop, customers={"c1"}))
    prop = _valid(_quote(items=[{"catalog_item_id": "i1", "qty": 1}], valid_days=61))
    assert any("valid_days" in e for e in _v(prop, customers={"c1"}))
    prop = _valid(_quote(items=[{"catalog_item_id": "i1", "qty": 1}],
                         note="bak https://ornek.com"))
    assert any("URL" in e for e in _v(prop, customers={"c1"}))


def test_quote_floor_refused_client_side():
    below = _valid(_quote(items=[{"catalog_item_id": "i1", "qty": 1, "unit_price": 1749}]))
    errs = _v(below, customers={"c1"})
    assert any("below half the catalog price" in e for e in errs)  # before any network call
    at_floor = _valid(_quote(items=[{"catalog_item_id": "i1", "qty": 1, "unit_price": 1750}]))
    assert _v(at_floor, customers={"c1"}) == []
    omitted = _valid(_quote(items=[{"catalog_item_id": "i1", "qty": 3}]))
    assert _v(omitted, customers={"c1"}) == []  # omitted unit price = catalog price


# --------------------------------------------------------------------------- #
# validation: teach_chatbot
# --------------------------------------------------------------------------- #

def _teach(**kw) -> dict:
    base = {"type": "teach_chatbot", "question": "Kurulum ne kadar sürer?",
            "answer": "Kurulum 2.500 TL, 1 iş günü sürer."}
    base.update(kw)
    return base


def test_teach_lengths_and_presence():
    assert any("question is required" in e
               for e in _v(_valid(_teach(question="  "))))
    assert any("answer is required" in e for e in _v(_valid(_teach(answer=None))))
    assert any("question too long" in e for e in _v(_valid(_teach(question="S" * 201))))
    assert any("answer too long" in e for e in _v(_valid(_teach(answer="C" * 1001))))
    assert _v(_valid(_teach())) == []  # 2.500 TL is the catalog price of Kurulum


@pytest.mark.parametrize("bad, kind", [
    ("bak https://ornek.com/x", "URL"), ("yaz info@dukkan.com", "e-mail"),
    ("ara 0532 123 45 67", "phone"), ("TR330006100519786457841326", "IBAN")])
def test_teach_contacts_rejected_in_question_and_answer(bad, kind):
    assert any(kind in e for e in _v(_valid(_teach(question=bad)))), bad
    assert any(kind in e for e in _v(_valid(_teach(answer=bad)))), bad


def test_teach_wrong_price_refused():
    prop = _valid(_teach(answer="Kurulum 1.000 TL, hemen yapılır."))
    errs = _v(prop)
    assert any("1000 TL" in e and "must not learn wrong prices" in e for e in errs), errs
    # A price the plan itself introduces (new item earlier) is fine.
    prop = {"summary_tr": "yeni ürün ve chatbot eğitimi", "actions": [
        {"type": "add_catalog_item", "item_type": "product", "ref": "n1", "name": "Fön Spreyi",
         "price": 900, "label": "ürün"},
        {"type": "teach_chatbot", "question": "Sprey ne kadar?", "answer": "Fön spreyi 900 TL.",
         "label": "chatbot"}]}
    assert _v(prop) == []
    # Bare numbers without a currency are not prices.
    assert _v(_valid(_teach(answer="Kurulum 1 iş günü sürer, 2 personel gelir."))) == []


# --------------------------------------------------------------------------- #
# cards + progress in both owner languages
# --------------------------------------------------------------------------- #

_LOG = [
    {"tool": "find_appointments", "result": {"appointments": [
        {"id": "a1", "customer": "Murat Demir", "service": "Kurulum + Eğitim",
         "starts": "2026-10-29T10:00", "company": "Ada Kafe"}]}},
    {"tool": "find_customer", "result": {"matches": [
        {"id": "c1", "name": "Murat Demir", "company": "Ada Kafe"}]}},
    {"tool": "find_leads", "result": {"leads": [
        {"id": "l1", "name": "Murat Demir", "stage": "proposal", "company": "Lavanta Spa"}]}},
    {"tool": "list_catalog", "result": {"items": [
        {"id": "i1", "name": "KEDD Büyüme", "price": 3500},
        {"id": "i2", "name": "Kurulum", "price": 2500}]}},
]


def _card(acts, lang, log=None) -> str:
    return render_card({"summary_tr": "özet", "actions": acts}, _ctx(), {},
                       card_facts(log if log is not None else _LOG), lang)


def test_money_cards_tr():
    card = _card([
        {"type": "complete_appointment", "appointment_id": "a1", "amount": 2500,
         "method": "transfer"},
        {"type": "create_quote_draft", "lead_id": "l1", "items": [
            {"catalog_item_id": "i1", "qty": 1}, {"catalog_item_id": "i2", "qty": 1}]},
        {"type": "teach_chatbot", "question": "Kurulum ne kadar sürer?",
         "answer": "1 iş günü."},
    ], "tr")
    assert ("✔️ Randevu tamamlandı: Murat Demir (Ada Kafe) — Kurulum + Eğitim, "
            "2,500 ₺ havale") in card
    assert ("📄 Teklif taslağı: Murat Demir (Lavanta Spa) — KEDD Büyüme ×1 + Kurulum ×1 "
            "= 6,000 ₺ (gönderilmez, taslak)") in card
    assert "🧠 Chatbot öğrenecek: “Kurulum ne kadar sürer?” → “1 iş günü.”" in card


def test_money_cards_en():
    card = _card([
        {"type": "complete_appointment", "appointment_id": "a1", "amount": 2500,
         "method": "transfer"},
        {"type": "create_quote_draft", "customer_id": "c1", "items": [
            {"catalog_item_id": "i1", "qty": 2, "unit_price": 3000}]},
        {"type": "teach_chatbot", "question": "How long?", "answer": "One day."},
    ], "en")
    assert "✔️ Appointment completed: Murat Demir (Ada Kafe)" in card
    assert "2,500 ₺ transfer" in card
    assert "📄 Quote draft: Murat Demir (Ada Kafe) — KEDD Büyüme ×2 = 6,000 ₺ " in card
    assert "(not sent, draft)" in card
    assert "🧠 Chatbot will learn: “How long?” → “One day.”" in card


def test_company_shown_wherever_a_customer_or_lead_is_named():
    card = _card([
        {"type": "create_appointment", "customer_id": "c1", "item_ref": "i2", "staff": "Efe",
         "starts": "2026-10-31T15:00"},
        {"type": "reschedule_appointment", "appointment_id": "a1",
         "starts": "2026-10-30T11:00"},
        {"type": "cancel_appointment", "appointment_id": "a1"},
        {"type": "adjust_loyalty", "customer_id": "c1", "points": 50, "reason": "r"},
        {"type": "update_customer", "customer_id": "c1", "add_tags": ["vip"]},
        {"type": "set_lead_stage", "lead_id": "l1", "stage": "won"},
    ], "tr")
    assert "📅 Randevu: Murat Demir (Ada Kafe) — Kurulum, Efe, 31.10 15:00" in card
    assert "🔁 Randevu taşınıyor: Murat Demir (Ada Kafe) — Kurulum + Eğitim," in card
    assert "❌ Randevu iptal: Murat Demir (Ada Kafe) — Kurulum + Eğitim," in card
    assert "⭐ Puan: Murat Demir (Ada Kafe) +50 (r)" in card
    assert "🏷️ Murat Demir (Ada Kafe): +etiket vip" in card
    assert "📈 Murat Demir (Lavanta Spa): teklif → kazanıldı" in card


def test_quote_overflow_answer_preview_and_free_completion():
    card = _card([
        {"type": "create_quote_draft", "customer_id": "c1", "items": [
            {"catalog_item_id": "i1", "qty": 1}, {"catalog_item_id": "i2", "qty": 1},
            {"catalog_item_id": "i1", "qty": 1}, {"catalog_item_id": "i2", "qty": 1},
            {"catalog_item_id": "i1", "qty": 1}]},
        {"type": "teach_chatbot", "question": "S?", "answer": "C" * 200},
        {"type": "complete_appointment", "appointment_id": "a1"},
    ], "tr")
    assert "KEDD Büyüme ×1 + Kurulum ×1 + KEDD Büyüme ×1 +2 kalem" in card
    assert "= 15,500 ₺" in card
    assert f"→ “{'C' * 119}…”" in card  # answer preview: first 120 chars
    assert "✔️ Randevu tamamlandı: Murat Demir (Ada Kafe) — Kurulum + Eğitim\n" in card


def test_schedule_seen_appointments_render_on_cards():
    thin = [{"tool": "get_schedule", "result": {"appointments": [
        {"id": "a9", "customer": "Elif Kaya", "service": "Demo Görüşmesi",
         "company": "Nar Kuaför"}]}}]
    card = _card([{"type": "complete_appointment", "appointment_id": "a9", "amount": 500}],
                 "tr", thin)
    assert "✔️ Randevu tamamlandı: Elif Kaya (Nar Kuaför) — Demo Görüşmesi, 500 ₺" in card
    # ...but a thin schedule row never clobbers a fuller find_appointments fact,
    # whichever tool ran later.
    clash = [{"tool": "get_schedule", "result": {"appointments": [
        {"id": "a1", "customer": "Yanlış İsim", "service": "Yanlış Servis"}]}}]
    act = [{"type": "reschedule_appointment", "appointment_id": "a1",
            "starts": "2026-10-30T11:00"}]
    assert "29.10 10:00 → 30.10 11:00" in _card(act, "tr", clash + _LOG)
    assert "29.10 10:00 → 30.10 11:00" in _card(act, "tr", _LOG + clash)


def test_money_progress_tr_and_en():
    steps = [
        {"action": "complete_appointment", "result": {"amount": 2500, "method": "transfer"}},
        {"action": "complete_appointment", "result": {"amount": None}},
        {"action": "create_quote_draft", "result": {"total": 6000}},
        {"action": "teach_chatbot", "result": {"question": "Kurulum ne kadar sürer?"}},
    ]
    tr = [i18n.render_progress("ok", i + 1, 4, s, "tr") for i, s in enumerate(steps)]
    assert tr[0] == "✅ 1/4 Randevu tamamlandı, 2,500 ₺ havale"
    assert tr[1] == "✅ 2/4 Randevu tamamlandı"
    assert tr[2] == "✅ 3/4 Teklif taslağı: 6,000 ₺"
    assert tr[3] == "✅ 4/4 Chatbot öğrendi: “Kurulum ne kadar sürer?”"
    en = [i18n.render_progress("ok", i + 1, 4, s, "en") for i, s in enumerate(steps)]
    assert en[0] == "✅ 1/4 Appointment completed, 2,500 ₺ transfer"
    assert en[2] == "✅ 3/4 Quote draft: 6,000 ₺"
    assert "Teklif hazırlanamadı" in i18n.render_progress(
        "failed", 1, 1, {"action": "create_quote_draft", "result": ""}, "tr")
    assert "Couldn't complete the appointment" in i18n.render_progress(
        "failed", 1, 1, {"action": "complete_appointment", "result": ""}, "en")


# --------------------------------------------------------------------------- #
# gateway request bodies + idempotency keys (offline, stubbed HTTP)
# --------------------------------------------------------------------------- #

def test_request_bodies_and_idempotency_keys():
    kb = _kb("appt_complete", "quote_create", "knowledge_create")
    kb.complete_appointment("appt-1", amount=2500, method="transfer", note="Elden.",
                            idempotency_key="k1")
    kb.create_quote(customer_id="c-9", items=[{"catalog_item_id": "item-1", "qty": 2}],
                    valid_days=30, note="n", idempotency_key="k2")
    kb.create_quote(lead_id="lead-1",
                    items=[{"catalog_item_id": "item-1", "qty": 1, "unit_price": 3000}],
                    idempotency_key="k3")
    kb.teach_chatbot("Soru?", "Cevap.", idempotency_key="k4")
    posts = [c for c in kb._s.calls if c[0] == "POST"]
    assert [(p[1][len(BASE):], p[3]) for p in posts] == [
        ("/agent/appointments/complete", {"appointment_id": "appt-1", "amount": 2500,
                                          "method": "transfer", "note": "Elden."}),
        ("/agent/quotes", {"customer_id": "c-9",
                           "items": [{"catalog_item_id": "item-1", "qty": 2}],
                           "valid_days": 30, "note": "n"}),
        ("/agent/quotes", {"lead_id": "lead-1",
                           "items": [{"catalog_item_id": "item-1", "qty": 1,
                                      "unit_price": 3000}]}),
        ("/agent/chatbot/knowledge", {"question": "Soru?", "answer": "Cevap."}),
    ]
    assert [p[2]["Idempotency-Key"] for p in posts] == ["k1", "k2", "k3", "k4"]
    assert "lead_id" not in posts[1][3]  # absent optionals are dropped, never null


def test_knowledge_search_query():
    kb = _kb("knowledge_search")
    res = kb.search_chatbot_knowledge("kurulum")
    assert kb._s.calls[0][1] == f"{BASE}/agent/chatbot/knowledge?q=kurulum"
    assert res["entries"] and res["truncated"] is False


@pytest.mark.parametrize("code,status,text", [
    ("not_found", 404, "Randevu bulunamadı"),
    ("already_cancelled", 409, "Bu randevu zaten iptal edilmiş"),
    ("already_completed", 409, "Bu randevu zaten tamamlanmış"),
    ("future_appointment", 400, "Randevu saati gelmeden tamamlanamaz"),
    ("event_reservation", 400, "Etkinlik rezervasyonları randevu gibi tamamlanamaz"),
    ("no_credits", 400, "Pakette kalan hak yok"),
    ("subscription_inactive", 400, "Abonelik aktif değil"),
    ("package_expired", 400, "Paketin süresi dolmuş"),
    ("service_not_allowed", 400, "Bu hizmet pakete dahil değil"),
])
def test_complete_errors_surfaced_in_turkish(code, status, text):
    kb = _kb(routes_extra={("POST", "/agent/appointments/complete"): (status, {"error": code})})
    with pytest.raises(ValueError, match=text):
        kb.complete_appointment("appt-1", amount=100)


def test_gateway_message_stays_verbatim_and_no_assistant_maps():
    kb = _kb(routes_extra={
        ("POST", "/agent/appointments/complete"):
            (409, {"error": "already_completed", "message": "Bu randevu çoktan kapanmış"})})
    with pytest.raises(ValueError, match="Bu randevu çoktan kapanmış"):
        kb.complete_appointment("appt-1")
    kb = _kb(routes_extra={
        ("POST", "/agent/chatbot/knowledge"): (409, {"error": "no_assistant"})})
    with pytest.raises(ValueError, match="chatbot asistanı kurulu değil"):
        kb.teach_chatbot("S?", "C.")


def test_adapter_rejects_two_quote_parties_before_network():
    kb = _kb("quote_create")
    with pytest.raises(ValueError, match="ya müşteri ya lead"):
        kb.create_quote(customer_id="c-9", lead_id="lead-1", items=[])
    assert kb._s.calls == []


# --------------------------------------------------------------------------- #
# mock offline behaviour: revenue, quotes, knowledge, company search
# --------------------------------------------------------------------------- #

def test_mock_completion_writes_revenue_the_insights_see():
    m = MockBackend("salon", today=DAY0)
    a = next(x for x in m.appointments if x.status == "confirmed")
    before = m.get_insights(1)
    res = m.complete_appointment(a.id, amount=2500, method="card")
    assert res == {"id": a.id, "status": "completed", "revenue_id": res["revenue_id"],
                   "amount": 2500.0}
    after = m.get_insights(1)
    assert after["revenue"]["paid_total"] - before["revenue"]["paid_total"] == 2500.0
    assert after["revenue"]["paid_count"] - before["revenue"]["paid_count"] == 1
    assert after["appointments"]["completed"] - before["appointments"]["completed"] == 1
    cust = next(c for c in m.customers if c.id == a.customer_id)
    assert cust.last_visit == DAY0.isoformat() and cust.total_spent >= 2500.0
    # Free/package completion: done, but no revenue row.
    b = next(x for x in m.appointments if x.status == "confirmed")
    free = m.complete_appointment(b.id)
    assert free == {"id": b.id, "status": "completed", "revenue_id": None, "amount": None}
    assert m.get_insights(1)["revenue"]["paid_count"] == after["revenue"]["paid_count"]
    # Double completion and unknown ids speak Turkish.
    with pytest.raises(ValueError, match="zaten tamamlanmış"):
        m.complete_appointment(a.id)
    with pytest.raises(ValueError, match="Randevu bulunamadı"):
        m.complete_appointment("a-nope")
    m.cancel_appointment(next(x for x in m.appointments if x.status == "confirmed").id)
    cancelled = next(x for x in m.appointments if x.status == "cancelled")
    with pytest.raises(ValueError, match="İptal edilmiş"):
        m.complete_appointment(cancelled.id)


def test_mock_quotes_total_and_floor():
    m = MockBackend("salon", today=DAY0)
    item = m.items[0]
    q = m.create_quote(customer_id=m.customers[0].id, items=[
        {"catalog_item_id": item.id, "qty": 2},
        {"catalog_item_id": m.items[1].id, "qty": 1, "unit_price": m.items[1].price}])
    assert q["total"] == round(2 * item.price + m.items[1].price, 2)
    assert q["status"] == "draft" and len(m.quotes) == 1
    with pytest.raises(ValueError, match="yarısından"):
        m.create_quote(lead_id=m.leads[0].id, items=[
            {"catalog_item_id": item.id, "qty": 1, "unit_price": item.price / 2 - 1}])
    with pytest.raises(ValueError, match="ya müşteri ya lead"):
        m.create_quote(items=[{"catalog_item_id": item.id, "qty": 1}])
    with pytest.raises(ValueError, match="en az bir kalem"):
        m.create_quote(customer_id=m.customers[0].id, items=[])


def test_mock_knowledge_roundtrip_and_truncation():
    m = MockBackend("salon", today=DAY0)
    assert m.search_chatbot_knowledge("ödeme")["entries"]  # seeded
    assert m.search_chatbot_knowledge("zzz-kayıt-dışı")["entries"] == []
    m.teach_chatbot("Kargo kaç gün?", "2 iş günü.")
    hits = m.search_chatbot_knowledge("KaRgO")["entries"]
    assert len(hits) == 1 and hits[0]["answer"] == "2 iş günü."
    for i in range(25):
        m.teach_chatbot(f"Soru {i} kargo?", f"Cevap {i}.")
    res = m.search_chatbot_knowledge("kargo")
    assert len(res["entries"]) == 20 and res["truncated"] is True
    with pytest.raises(ValueError, match="soru ve cevap"):
        m.teach_chatbot("  ", "C.")


def test_mock_company_search_and_schedule_ids():
    m = MockBackend("salon", today=DAY0)
    assert m.find_customer("ada kafe")  # company match, accent-insensitive
    assert all("company" in c for c in m.find_customer("ayşe"))
    assert all("company" in row for row in m.find_appointments())
    day = m.appointments[0].starts[:10]
    rows = m.get_schedule(day)["appointments"]
    assert rows and all({"id", "company"} <= set(r) for r in rows)


# --------------------------------------------------------------------------- #
# executor + agent wiring
# --------------------------------------------------------------------------- #

def test_executor_runs_money_actions_with_progress():
    m = MockBackend("salon", today=DAY0)
    svc = next(i for i in m.items if i.type == "service")
    day = (DAY0 + timedelta(days=1)).isoformat()
    appt = m.create_appointment(m.customers[0].id, svc.id, m.profile["staff"][0],
                                f"{day}T10:00")
    events: list[tuple] = []
    prop = _valid(
        {"type": "complete_appointment", "appointment_id": appt["id"], "amount": 1500,
         "method": "cash"},
        {"type": "create_quote_draft", "customer_id": m.customers[0].id,
         "items": [{"catalog_item_id": svc.id, "qty": 1}]},
        {"type": "teach_chatbot", "question": "S?", "answer": "C."},
    )
    assert _v(prop, catalog={i["id"]: i for i in m.list_catalog()},
              appts={appt["id"]}, customers={m.customers[0].id}) == []
    steps = executor.execute(prop, _ctx(), m,
                             on_step=lambda e, i, n, s: events.append((e, i, s)))
    assert [s["status"] for s in steps] == ["ok", "ok", "ok"]
    assert steps[0]["result"]["amount"] == 1500.0
    assert steps[1]["result"]["total"] == svc.price
    assert [e[0] for e in events] == ["start", "ok", "ok", "ok"]
    line = i18n.render_progress("ok", 1, 3, steps[0], "tr")
    assert line == "✅ 1/3 Randevu tamamlandı, 1,500 ₺ nakit"


def test_agent_tracks_schedule_ids_services_and_search_tool():
    m = MockBackend("salon", today=DAY0)
    run = AgentRun()
    day = m.appointments[0].starts[:10]
    sched = _run_tool("get_schedule", {"day": day}, m, run)
    assert sched["appointments"] and run.appointments
    assert run.appointment_services
    assert set(run.appointment_services) <= run.appointments
    found = _run_tool("find_appointments", {"day": day}, m, run)
    assert found["appointments"]
    res = _run_tool("search_chatbot_knowledge", {"q": "ödeme"}, m, run)
    assert res["entries"]
    assert "search_chatbot_knowledge" in {t["name"] for t in TOOLS}
    assert "search_chatbot_knowledge" in SYSTEM_PROMPT
    assert "bu zaten öğretilmiş" in SYSTEM_PROMPT
    assert "get_insights(days=1)" in SYSTEM_PROMPT
    assert "Name (Company)" in SYSTEM_PROMPT
    assert "never sent" in SYSTEM_PROMPT


def test_coverage_lists_completion_quotes_chatbot_as_live():
    cov_path = Path(__file__).parent.parent / "app" / "static" / "coverage.json"
    cov = json.loads(cov_path.read_text())
    live = {c["area"] for c in cov if c["status"] == "live"}
    assert {"completion", "quotes", "chatbot"} <= live
    what = " ".join(c["what_en"] for c in cov)
    assert "Complete appointments + revenue" in what
    assert "Quote drafts" in what and "Teach the AI chatbot" in what
