"""Proactive morning brief: fixed instruction through core.handle, greeting, scheduled loop."""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import agent, brief, core, report, worker
from app.channels import web as webchan
from app.config import settings
from app.tools.backend import MockBackend


@pytest.fixture
def cfg():
    saved = {}

    def _set(name, value):
        saved.setdefault(name, getattr(settings, name))
        object.__setattr__(settings, name, value)
    yield _set
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def _salon(tmp_path):
    return core.Session(sender="905321",
                        backend=MockBackend("salon", today=date(2026, 10, 27), store_path=tmp_path / "s"))


def _answering(seen, text):
    def _plan(ctx, backend, max_revisions=3):
        seen.append(ctx)
        return SimpleNamespace(proposal=None, answer=text, tool_log=[], turns=1, usage={})
    return _plan


def _proposing():
    def _plan(ctx, backend, max_revisions=3):
        seg = backend.find_customers(inactive_days=60)
        return SimpleNamespace(
            proposal={"summary_tr": "Sabah kampanyası.", "actions": [
                {"type": "send_campaign", "segment_id": seg["segment_id"], "message": "Öğleden sonra %15!"}]},
            answer=None, tool_log=[{"tool": "find_customers", "args": {}, "result": seg}],
            turns=1, usage={})
    return _plan


def test_brief_runs_through_core_handle(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    seen = []
    monkeypatch.setattr(agent, "plan", _answering(seen, "Bugün 9 randevun var."))
    s = _salon(tmp_path)
    replies = brief.build_morning_brief(s)
    assert len(seen) == 1
    assert seen[0].instruction == brief.BRIEF_INSTRUCTION and seen[0].instruction_source == "text"
    assert [h["role"] for h in s.history] == ["owner", "cirak"]
    assert s.history[0]["text"] == brief.BRIEF_INSTRUCTION
    assert replies[0].text.startswith("Günaydın ☀️ ")


def test_brief_greeting_prefix_only_when_missing(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _answering([], "Bugün 9 randevun var, 14:00 boş."))
    assert brief.build_morning_brief(_salon(tmp_path))[0].text.startswith("Günaydın ☀️ Bugün")

    monkeypatch.setattr(agent, "plan", _answering([], "Günaydın! Bugün 9 randevun var."))
    assert brief.build_morning_brief(_salon(tmp_path))[0].text == "Günaydın! Bugün 9 randevun var."

    monkeypatch.setattr(agent, "plan", _answering([], "☀️ Günaydın, bugün sakinsin."))
    assert brief.build_morning_brief(_salon(tmp_path))[0].text == "☀️ Günaydın, bugün sakinsin."


def test_brief_card_is_pending_and_needs_evet(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _proposing())
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep: "Hallettim.")
    s = _salon(tmp_path)
    r = brief.build_morning_brief(s)
    assert s.pending is not None and r[0].text.startswith("Günaydın ☀️ *Çırak")
    assert s.backend.campaigns == []                                     # the card alone sends nothing
    assert core.handle(s, "text", "evet")[0].text.startswith("Hallettim")
    assert len(s.backend.campaigns) == 1


def test_brief_loop_failure_does_not_stop_others(monkeypatch, cfg):
    cfg("wa_allowed_senders", frozenset({"905321", "905322"}))
    store, delivered = {}, []

    def fake_build(session):
        if session.sender == "905321":
            raise RuntimeError("model down")
        return [core.Reply("Günaydın ☀️ ok")]

    monkeypatch.setattr(brief, "build_morning_brief", fake_build)
    monkeypatch.setattr(worker, "deliver_replies",
                        lambda sender, replies: delivered.append(sender) or [])
    results = brief.send_morning_briefs(store.get, store.__setitem__)
    assert results == {"905321": "failed: model down", "905322": "sent"}
    assert delivered == ["905322"] and sorted(store) == ["905321", "905322"]


def test_brief_loop_skips_24h_window_and_empty_list(monkeypatch, cfg):
    cfg("wa_allowed_senders", frozenset({"905321", "905322"}))
    window_err = RuntimeError("400 Client Error")
    window_err.response = SimpleNamespace(
        json=lambda: {"error": {"code": 131047, "message": "(#131047) Re-engagement message"}})
    monkeypatch.setattr(brief, "build_morning_brief", lambda session: [core.Reply("Günaydın ☀️ ok")])
    monkeypatch.setattr(worker, "deliver_replies",
                        lambda sender, replies: [window_err] if sender == "905321" else [])
    store: dict = {}
    assert brief.send_morning_briefs(store.get, store.__setitem__) == {
        "905321": "skipped-24h", "905322": "sent"}

    cfg("wa_allowed_senders", frozenset())
    assert brief.send_morning_briefs(store.get, store.__setitem__) == {}


def test_deliver_replies_attempts_all(monkeypatch):
    calls = []

    def fake_send(to, text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("flaky network")

    monkeypatch.setattr(worker.wa, "send_text", fake_send)
    errors = worker.deliver_replies("905321", [core.Reply("bir"), core.Reply("iki")])
    assert calls == ["bir", "iki"] and len(errors) == 1


def test_api_brief_returns_greeted_replies(monkeypatch, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(agent, "plan", _answering([], "Bugün 9 randevun var."))
    client = TestClient(webchan.create_app())
    assert "Sabah özeti" in client.get("/").text                       # the quick button is there
    r = client.post("/api/brief")
    assert r.status_code == 200
    assert r.json()["replies"][0]["text"].startswith("Günaydın ☀️ Bugün")
