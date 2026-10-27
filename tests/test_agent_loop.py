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
