"""Exercise the agent loop against a scripted fake of the Responses API (no network)."""

import json
from datetime import date
from types import SimpleNamespace

from app import agent
from app.pipeline import MediaContext, ProductCard
from app.tools.backend import MockBackend


class FakeResponses:
    """Scripted model: 1) asks for profile + segment, 2) proposes invalid plan, 3) fixes it."""

    def __init__(self):
        self.calls = []
        self.step = 0

    def _resp(self, output):
        return SimpleNamespace(id=f"resp_{self.step}", output=output, output_text="",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    @staticmethod
    def _fc(name, args, cid):
        return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=cid)

    def create(self, **kw):
        self.calls.append(kw)
        self.step += 1
        if self.step == 1:
            assert kw["tools"] and "instructions" in kw
            return self._resp([self._fc("get_store_profile", {}, "c1"),
                               self._fc("find_customers", {"inactive_days": 60}, "c2")])
        if self.step == 2:
            outs = kw["input"]
            assert {o["call_id"] for o in outs} == {"c1", "c2"} and kw["previous_response_id"] == "resp_1"
            seg = json.loads(outs[1]["output"])["segment_id"]
            self.seg = seg
            return self._resp([self._fc("propose_actions", {
                "summary_tr": "plan", "actions": [
                    {"type": "add_catalog_item", "item_type": "product", "temp_id": "tmp1", "name": "Çanta", "price": -5},
                    {"type": "send_campaign", "segment_id": seg, "message": "selam"}]}, "c3")])
        if self.step == 3:
            err = json.loads(kw["input"][0]["output"])
            assert err["accepted"] is False and any("price" in e for e in err["errors"])
            return self._resp([self._fc("propose_actions", {
                "summary_tr": "plan", "actions": [
                    {"type": "add_catalog_item", "item_type": "product", "temp_id": "tmp1", "name": "Çanta", "price": 850},
                    {"type": "send_campaign", "segment_id": self.seg, "message": "selam"}]}, "c4")])
        raise AssertionError("too many steps")


class FakeAnswering:
    """Scripted model: reads the daily summary, then answers with reply_only (no plan)."""

    def __init__(self):
        self.step = 0

    def create(self, **kw):
        self.step += 1
        fc = FakeResponses._fc
        resp = lambda out: SimpleNamespace(id=f"r{self.step}", output=out, output_text="",  # noqa: E731
                                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        if self.step == 1:
            return resp([fc("get_daily_summary", {"day": "2026-10-27"}, "c1")])
        if self.step == 2:
            summary = json.loads(kw["input"][0]["output"])
            assert summary["revenue"]["total"] > 0
            return resp([fc("reply_only", {"text": f"Bugün ciro {summary['revenue']['total']:.0f} TL 👍"}, "c2")])
        raise AssertionError("too many steps")


def test_agent_loop_reply_only(monkeypatch):
    monkeypatch.setattr(agent.meta, "client", lambda: SimpleNamespace(responses=FakeAnswering()))
    run = agent.plan(MediaContext("Bugün ne oldu?", "voice"), MockBackend("salon", today=date(2026, 10, 27)))
    assert run.proposal is None and run.answer.startswith("Bugün ciro") and run.turns == 2


class FakeCapture:
    """Records the first user message, then answers with reply_only."""

    def __init__(self):
        self.inputs: list[str] = []

    def create(self, **kw):
        if isinstance(kw.get("input"), str):
            self.inputs.append(kw["input"])
        fc = FakeResponses._fc
        return SimpleNamespace(id="r1", output=[fc("reply_only", {"text": "tamam"}, "c1")],
                               output_text="", usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def test_recent_conversation_reaches_prompt_only_when_nonempty(monkeypatch):
    assert "recent_conversation" not in MediaContext("selam", "text").to_prompt_json()
    hist = [{"role": "owner", "text": "randevumu al", "at": 1.0},
            {"role": "cirak", "text": "17:15'e alayım mı?", "at": 2.0}]
    assert MediaContext("olur", "text", history=hist).to_prompt_json()["recent_conversation"] == hist

    assert "never treat 'olur' as an approval by itself" in agent.SYSTEM_PROMPT

    fake = FakeCapture()
    monkeypatch.setattr(agent.meta, "client", lambda: SimpleNamespace(responses=fake))
    backend = MockBackend("salon", today=date(2026, 10, 27))
    agent.plan(MediaContext("olur", "text", history=hist), backend)
    agent.plan(MediaContext("selam", "text"), backend)
    assert '"recent_conversation"' in fake.inputs[0] and "17:15'e alayım mı?" in fake.inputs[0]
    assert '"recent_conversation"' not in fake.inputs[1]


class FakeBulk:
    """Scripted model: previews a bulk change on the real backend, then applies it."""

    def __init__(self):
        self.step = 0

    def create(self, **kw):
        self.step += 1
        fc = FakeResponses._fc
        resp = lambda out: SimpleNamespace(id=f"b{self.step}", output=out, output_text="",  # noqa: E731
                                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        if self.step == 1:
            return resp([fc("preview_bulk_price", {"category": "elbise", "mode": "percent", "value": 10},
                            "c1")])
        if self.step == 2:
            pv = json.loads(kw["input"][0]["output"])
            assert pv["count"] == 1 and pv["items"][0]["new_price"] == 1419.0
            return resp([fc("propose_actions", {
                "summary_tr": "Elbiselere %10 zam.", "actions": [
                    {"type": "apply_bulk_price", "preview_id": pv["preview_id"],
                     "label": "1 kalem (Midi Elbise 1290→1419 ₺)"}]}, "c2")])
        raise AssertionError("too many steps")


class FakeAds:
    """Scripted model: reads ad performance, then pauses the loser."""

    def __init__(self):
        self.step = 0

    def create(self, **kw):
        self.step += 1
        fc = FakeResponses._fc
        resp = lambda out: SimpleNamespace(id=f"d{self.step}", output=out, output_text="",  # noqa: E731
                                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        if self.step == 1:
            return resp([fc("get_ads_performance", {"days": 7}, "c1"),
                         fc("get_ads_suggestions", {}, "c2")])
        if self.step == 2:
            perf = json.loads(kw["input"][0]["output"])
            assert len(perf["campaigns"]) == 3 and "totals" in perf
            return resp([fc("propose_actions", {
                "summary_tr": "Kötü giden reklam duraklatılıyor.", "actions": [
                    {"type": "ads_action", "campaign_id": "ad2", "action": "pause",
                     "label": "Deri Çanta duraklat"}]}, "c3")])
        raise AssertionError("too many steps")


def test_agent_loop_bulk_price_preview_then_apply(monkeypatch):
    monkeypatch.setattr(agent.meta, "client", lambda: SimpleNamespace(responses=FakeBulk()))
    run = agent.plan(MediaContext("Tüm elbiselere %10 zam yap", "text"),
                     MockBackend("butik", today=date(2026, 10, 27)))
    assert run.proposal["actions"][0]["type"] == "apply_bulk_price"
    assert len(run.previews) == 1 and run.turns == 2


def test_agent_loop_ads_pause_loser(monkeypatch):
    monkeypatch.setattr(agent.meta, "client", lambda: SimpleNamespace(responses=FakeAds()))
    run = agent.plan(MediaContext("En kötü giden reklamı durdur", "text"),
                     MockBackend("butik", today=date(2026, 10, 27)))
    assert run.proposal["actions"][0]["action"] == "pause"
    assert run.campaigns == {"ad1", "ad2", "ad3"} and run.turns == 2


class FakeTemplate:
    """Scripted model: lists templates + builds a segment, then proposes a template campaign."""

    def __init__(self):
        self.step = 0

    def create(self, **kw):
        self.step += 1
        fc = FakeResponses._fc
        resp = lambda out: SimpleNamespace(id=f"t{self.step}", output=out, output_text="",  # noqa: E731
                                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        if self.step == 1:
            return resp([fc("get_store_profile", {}, "c1"),
                         fc("find_customers", {"inactive_days": 60}, "c2"),
                         fc("list_campaign_templates", {}, "c3")])
        if self.step == 2:
            outs = {o["call_id"]: json.loads(o["output"]) for o in kw["input"]}
            assert "marketing_reengagement_promo_tr" in [t["name"] for t in outs["c3"]["templates"]]
            return resp([fc("propose_actions", {
                "summary_tr": "Gelmeyenlere şablon kampanya.", "actions": [
                    {"type": "send_campaign", "segment_id": outs["c2"]["segment_id"],
                     "template": "marketing_reengagement_promo_tr",
                     "variables": {"2": "FULAR15", "3": "hafta sonu fırsatı"}}]}, "c4")])
        raise AssertionError("too many steps")


def test_agent_loop_template_campaign(monkeypatch):
    monkeypatch.setattr(agent.meta, "client", lambda: SimpleNamespace(responses=FakeTemplate()))
    run = agent.plan(MediaContext("uzun süredir gelmeyenlere haber ver", "text"),
                     MockBackend("butik", today=date(2026, 10, 27)))
    assert run.proposal["actions"][0]["template"] == "marketing_reengagement_promo_tr"
    assert len(run.templates) == 3 and run.turns == 2


def test_agent_loop_revises_invalid_proposal(monkeypatch):
    fake = FakeResponses()
    monkeypatch.setattr(agent.meta, "client", lambda: SimpleNamespace(responses=fake))
    backend = MockBackend("butik", today=date(2026, 10, 27))
    ctx = MediaContext("Bunları ekle 850 lira", "text",
                       [ProductCard("tmp1", "x.png", "s", "Çanta", "çanta", "siyah", "deri", "d")])
    run = agent.plan(ctx, backend)
    assert run.proposal["actions"][0]["price"] == 850
    assert run.turns == 3 and run.segments == {fake.seg}
    assert [t["tool"] for t in run.tool_log] == ["get_store_profile", "find_customers", "propose_actions", "propose_actions"]
