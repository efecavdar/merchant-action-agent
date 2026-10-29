"""Task 20: campaigns, loyalty and ads call the live KEDD gateway.

Template sends with a planning-time dry run, loyalty member lookup + adjust, ads
actions with delta→absolute budget mapping. The loyalty members endpoint is not
deployed yet: these tests pin the brief's fixture shape. All offline (stubbed HTTP).
"""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import actions, agent, approval, executor
from app.agent import AgentRun
from app.pipeline import MediaContext
from app.tools.backend import KeddBackend, MockBackend

FIX = Path(__file__).parent / "fixtures" / "kedd"
BASE = "https://kedd.example"
DAY0 = date(2026, 10, 27)
TMPL = "marketing_reengagement_promo_tr"
VARS = {"2": "FULAR15", "3": "hafta sonu %15"}


def _load(name: str):
    return json.loads((FIX / f"{name}.json").read_text())


class _Stub:
    """Serve one payload per (method, gateway path); record traffic."""

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


def _kb(*names: str, key_id: str = "main", routes_extra: dict | None = None) -> KeddBackend:
    post = {"campaigns_dryrun", "campaigns_send", "loyalty_adjust", "ads_action", "ads_pause",
            "segments"}
    routes = {(("POST" if n in post else "GET"), _P[n]): _load(n) for n in names}
    routes.update(routes_extra or {})
    kb = KeddBackend(BASE, "s3cret", "+905321", key_id=key_id)
    kb._s = _Stub(routes)
    return kb


_P = {
    "campaign_templates": "/agent/campaign-templates",
    "campaigns_dryrun": "/agent/campaigns",
    "campaigns_send": "/agent/campaigns",
    "segments": "/agent/segments",
    "loyalty_members": "/agent/loyalty/members",
    "loyalty_adjust": "/agent/loyalty/adjust",
    "ads_performance": "/agent/ads/performance",
    "ads_action": "/agent/ads/action",
    "ads_pause": "/agent/ads/action",
}


def _posts(stub: _Stub, path: str):
    return [c for c in stub.calls if c[0] == "POST" and c[1] == f"{BASE}{path}"]


# --------------------------------------------------------------------------- #
# campaigns: dry-run preview at planning time, template send at execution
# --------------------------------------------------------------------------- #

def test_t20_send_posts_named_variables_and_reports_counts():
    kb = _kb("campaign_templates", "campaigns_send")
    res = kb.send_campaign("Kış", "seg-9", template=TMPL, variables=dict(VARS),
                           idempotency_key="run-0")
    posts = _posts(kb._s, "/agent/campaigns")
    assert len(posts) == 1
    _m, _u, headers, body = posts[0]
    assert body == {"segment_id": "seg-9", "template": TMPL,
                    "variables": {"code": "FULAR15", "advantage": "hafta sonu %15"},
                    "dry_run": False}
    assert headers["Idempotency-Key"] == "run-0"
    assert res["recipients"] == 23 and res["status"] == "sent" and res["template"] == TMPL
    assert "21 gönderildi" in res["note"] and "2 atlandı" in res["note"]
    assert "‹müşteri adı›" in res["message"] and "{{1}}" not in res["message"]


def test_t20_send_requires_a_template():
    kb = _kb("campaign_templates", "campaigns_send")
    with pytest.raises(ValueError, match="şablon"):
        kb.send_campaign("Eski", "seg9", "merhaba", "")
    assert kb._s.calls == []


def test_t20_send_rejects_unknown_template_and_variable():
    kb = _kb("campaign_templates", "campaigns_send")
    with pytest.raises(ValueError, match="şablon bulunamadı"):
        kb.send_campaign("T", "seg-9", template="nope", variables={})
    assert _posts(kb._s, "/agent/campaigns") == []
    with pytest.raises(ValueError, match="değişken"):
        kb.send_campaign("T", "seg-9", template=TMPL, variables={"9": "x"})
    assert _posts(kb._s, "/agent/campaigns") == []


def test_t20_preview_masks_the_first_customer_name():
    kb = _kb("segments", "campaign_templates", "campaigns_dryrun")
    seg = kb.find_customers(inactive_days=60)  # samples cached for masking
    res = kb.preview_campaign(seg["segment_id"], TMPL, dict(VARS))
    assert res["template_text"] == ("Merhaba ‹müşteri adı›! Hafta sonu %15 seni bekliyor, "
                                    "kodun: FULAR15.")
    assert res["recipients"] == 23 and res["dry_run"] is True
    assert res["skipped"] == {"no_consent": 4, "other": 0}
    body = _posts(kb._s, "/agent/campaigns")[0][3]
    assert body["dry_run"] is True and body["variables"] == {"code": "FULAR15",
                                                             "advantage": "hafta sonu %15"}


def test_t20_preview_masks_an_unfilled_placeholder():
    kb = _kb("segments", "campaign_templates", "campaigns_dryrun",
             routes_extra={("POST", "/agent/campaigns"): {**_load("campaigns_dryrun"),
                                               "template_text": "Merhaba {{1}}!"}})
    seg = kb.find_customers(inactive_days=60)
    res = kb.preview_campaign(seg["segment_id"], TMPL, dict(VARS))
    assert res["template_text"] == "Merhaba ‹müşteri adı›!"


@pytest.mark.parametrize("code,status", [("segment_drift", 409), ("segment_used", 409),
                                         ("quiet_hours", 409), ("quota_exceeded", 409),
                                         ("invalid_variable", 400)])
def test_t20_campaign_errors_surface_turkish_verbatim(code, status):
    kb = _kb("campaign_templates",
             routes_extra={("POST", "/agent/campaigns"): (
                 status, {"error": code, "message": f"TR-{code}"})})
    with pytest.raises(ValueError, match=f"TR-{code}"):
        kb.send_campaign("T", "seg-9", template=TMPL, variables=dict(VARS),
                         idempotency_key="k")


# --------------------------------------------------------------------------- #
# loyalty: member lookup (fixture shape — endpoint not deployed yet) + adjust
# --------------------------------------------------------------------------- #

def test_t20_find_loyalty_member_maps_fixture_shape():
    kb = _kb("loyalty_members")
    res = kb.find_loyalty_member("Ayşe")
    assert res == {"members": [
        {"member_id": "m-1", "name": "Ayşe Yılmaz", "points": 450, "tier": "gold"},
        {"member_id": "m-2", "name": "Ayşe Demir", "points": 120, "tier": "silver"}]}
    assert kb._s.calls[0][1].startswith(f"{BASE}/agent/loyalty/members?name=")


def test_t20_adjust_posts_member_and_maps_response():
    kb = _kb("loyalty_adjust")
    res = kb.adjust_loyalty("m-1", 200, "doğum günü", idempotency_key="k-2")
    posts = _posts(kb._s, "/agent/loyalty/adjust")
    assert len(posts) == 1
    assert posts[0][3] == {"member_id": "m-1", "delta": 200, "reason": "doğum günü"}
    assert posts[0][2]["Idempotency-Key"] == "k-2"
    assert (res["customer_id"], res["old_points"], res["points"], res["delta"]) == (
        "m-1", 450, 650, 200)


@pytest.mark.parametrize("points", [0, 1500, -1500, True, "çok"])
def test_t20_adjust_refuses_bad_deltas_without_http(points):
    kb = _kb("loyalty_adjust")
    with pytest.raises(ValueError, match="puan"):
        kb.adjust_loyalty("m-1", points, "")
    assert kb._s.calls == []


def test_t20_mock_preview_and_member_lookup():
    m = MockBackend("salon", today=DAY0)
    seg = m.find_customers(min_visits=1)["segment_id"]
    pv = m.preview_campaign(seg, TMPL, dict(VARS))
    assert pv["dry_run"] is True and pv["recipients"] == len(m.segments[seg])
    assert pv["skipped"] == {"no_consent": 0, "other": 0}
    assert "‹müşteri adı›" in pv["template_text"] and "{{1}}" not in pv["template_text"]
    for cid in m.segments[seg]:
        assert next(c.name for c in m.customers if c.id == cid) not in pv["template_text"]
    with pytest.raises(ValueError, match="segment"):
        m.preview_campaign("seg-nope", TMPL, {})
    with pytest.raises(ValueError, match="template"):
        m.preview_campaign(seg, "nope", {})
    first = m.customers[0]
    found = m.find_loyalty_member(first.name.split()[0])
    assert {"member_id": first.id, "name": first.name, "points": first.loyalty_points,
            "tier": None} in found["members"]
    assert m.find_loyalty_member("Kimse Yok") == {"members": []}


# --------------------------------------------------------------------------- #
# ads: delta_pct → absolute budget from same-run performance data
# --------------------------------------------------------------------------- #

def test_t20_daily_budget_mapped_from_performance():
    kb = _kb("ads_performance")
    camps = {c["id"]: c for c in kb.ads_performance(7)["campaigns"]}
    assert camps["ad-1"]["daily_budget"] == 500.0
    assert camps["ad-2"]["daily_budget"] == 300.0


def test_t20_ads_pause_posts_without_budget():
    kb = _kb("ads_performance", "ads_pause")
    res = kb.ads_action("ad-2", "pause", reason="kötü gidiyor", idempotency_key="k-3")
    posts = _posts(kb._s, "/agent/ads/action")
    assert len(posts) == 1
    assert posts[0][3] == {"campaign_id": "ad-2", "action": "pause", "reason": "kötü gidiyor"}
    assert posts[0][2]["Idempotency-Key"] == "k-3"
    assert (res["id"], res["name"], res["status"], res["daily_budget"]) == (
        "ad-2", "Deri Çanta", "paused", 300.0)


def test_t20_ads_budget_maps_delta_to_absolute():
    kb = _kb("ads_performance", "ads_action")
    res = kb.ads_action("ad-1", "increase_budget", 10, reason="iyi gidiyor")
    body = _posts(kb._s, "/agent/ads/action")[0][3]
    assert body == {"campaign_id": "ad-1", "action": "set_budget", "budget": 550.0,
                    "reason": "iyi gidiyor"}
    assert (res["status"], res["daily_budget"]) == ("active", 550.0)
    kb2 = _kb("ads_performance", "ads_action")
    kb2.ads_action("ad-1", "decrease_budget", 20, reason="x")
    assert _posts(kb2._s, "/agent/ads/action")[0][3]["budget"] == 400.0


def test_t20_ads_refuses_big_or_missing_delta_without_http():
    kb = _kb("ads_performance", "ads_action")
    with pytest.raises(ValueError, match="1..20|20"):
        kb.ads_action("ad-1", "increase_budget", 25)
    with pytest.raises(ValueError, match="delta_pct"):
        kb.ads_action("ad-1", "increase_budget", None)
    with pytest.raises(ValueError, match="anlaşılmadı"):
        kb.ads_action("ad-1", "boost", None)
    assert kb._s.calls == []


def test_t20_ads_refuses_unknown_paused_or_budgetless():
    kb = _kb("ads_performance", "ads_action")
    with pytest.raises(ValueError, match="bulunamadı"):
        kb.ads_action("ad-nope", "pause")
    assert _posts(kb._s, "/agent/ads/action") == []
    paused = {"days": 7, "campaigns": [{**_load("ads_performance")["campaigns"][0],
                                       "status": "paused"}]}
    kb2 = _kb(routes_extra={("GET", "/agent/ads/performance"): paused,
                 ("POST", "/agent/ads/action"): _load("ads_action")})
    with pytest.raises(ValueError, match="Duraklatılmış"):
        kb2.ads_action("ad-1", "increase_budget", 10)
    assert _posts(kb2._s, "/agent/ads/action") == []
    nobudget = {"days": 7, "campaigns": [{k: v for k, v in
                                          _load("ads_performance")["campaigns"][0].items()
                                          if k != "dailyBudget"}]}
    kb3 = _kb(routes_extra={("GET", "/agent/ads/performance"): nobudget,
                 ("POST", "/agent/ads/action"): _load("ads_action")})
    with pytest.raises(ValueError, match="bütçe"):
        kb3.ads_action("ad-1", "increase_budget", 10)
    assert _posts(kb3._s, "/agent/ads/action") == []


def test_t20_ads_budget_fallback_key_and_ops_cap_and_reason():
    snaked = {"days": 7, "campaigns": [{**_load("ads_performance")["campaigns"][0],
                                       "daily_budget": 500.0}]}
    del snaked["campaigns"][0]["dailyBudget"]
    kb = _kb(routes_extra={("GET", "/agent/ads/performance"): snaked,
                ("POST", "/agent/ads/action"): _load("ads_action")})
    kb.ads_action("ad-1", "increase_budget", 10)
    body = _posts(kb._s, "/agent/ads/action")[0][3]
    assert body["budget"] == 550.0 and body["reason"]  # default reason when the plan has none
    long_reason = "x" * 300
    kb.ads_action("ad-1", "pause", reason=long_reason)
    assert len(_posts(kb._s, "/agent/ads/action")[1][3]["reason"]) == 200
    kb2 = _kb("ads_performance", routes_extra={("POST", "/agent/ads/action"): (
        409, {"error": "too_many_budget_ops", "message": "TR-budget-cap"})})
    with pytest.raises(ValueError, match="TR-budget-cap"):
        kb2.ads_action("ad-1", "increase_budget", 10)


# --------------------------------------------------------------------------- #
# demo key: preview yes, loyalty yes; sends and ads stay refused
# --------------------------------------------------------------------------- #

def test_t20_demo_key_never_sends_or_touches_ads():
    kb = _kb("segments", "campaign_templates", "campaigns_dryrun", "loyalty_adjust",
             key_id="demo")
    seg = kb.find_customers(inactive_days=60)["segment_id"]
    pv = kb.preview_campaign(seg, TMPL, dict(VARS))  # dry runs are the preview: allowed
    assert pv["dry_run"] is True
    kb.adjust_loyalty("m-1", 50, "demo")  # reversible loyalty write: allowed
    with pytest.raises(ValueError, match="kapalı"):
        kb.send_campaign("T", seg, template=TMPL, variables=dict(VARS))
    with pytest.raises(ValueError, match="kapalı"):
        kb.ads_action("ad-1", "pause")
    for _m, _u, _h, body in _posts(kb._s, "/agent/campaigns"):
        assert body["dry_run"] is True
    assert _posts(kb._s, "/agent/ads/action") == []


# --------------------------------------------------------------------------- #
# executor integration: the frozen caller passes stable keys and reads our keys
# --------------------------------------------------------------------------- #

def _run(actions_list, kb, tmp_path):
    ctx = MediaContext(instruction="x", instruction_source="text")
    return executor.execute({"summary_tr": "x", "actions": actions_list}, ctx, kb, tmp_path)


def test_t20_execute_sends_with_stable_idempotency_key(tmp_path):
    kb = _kb("campaign_templates", "campaigns_send")
    steps = _run([{"type": "send_campaign", "name": "Kış", "segment_id": "seg-9",
                   "template": TMPL, "variables": dict(VARS)}], kb, tmp_path)
    assert steps[0]["status"] == "ok" and steps[0]["result"]["recipients"] == 23
    assert _posts(kb._s, "/agent/campaigns")[0][2]["Idempotency-Key"] == f"{tmp_path.name}-0"


def test_t20_execute_loyalty_and_ads(tmp_path):
    kb = _kb("loyalty_adjust")
    steps = _run([{"type": "adjust_loyalty", "customer_id": "m-1", "points": 200,
                   "reason": "x"}], kb, tmp_path)
    assert steps[0]["status"] == "ok" and steps[0]["result"]["points"] == 650
    kb = _kb("ads_performance", "ads_action")
    steps = _run([{"type": "ads_action", "campaign_id": "ad-1", "action": "increase_budget",
                   "delta_pct": 10}], kb, tmp_path)
    assert steps[0]["status"] == "ok" and steps[0]["result"]["daily_budget"] == 550.0


# --------------------------------------------------------------------------- #
# planner tools + run-scoped ids + approval card facts
# --------------------------------------------------------------------------- #

def test_t20_tools_registered_and_dispatched():
    names = {t["name"] for t in agent.TOOLS}
    assert {"preview_campaign", "find_loyalty_member"} <= names
    descs = {t["name"]: t["description"] for t in agent.TOOLS}
    assert "send_campaign" in descs["preview_campaign"]
    assert "adjust_loyalty" in descs["find_loyalty_member"]
    m = MockBackend("salon", today=DAY0)
    run = AgentRun()
    seg = agent._run_tool("find_customers", {"min_visits": 1}, m, run)["segment_id"]
    pv = agent._run_tool("preview_campaign",
                         {"segment_id": seg, "template": TMPL, "variables": dict(VARS)}, m, run)
    assert pv["recipients"] >= 0 and "‹müşteri adı›" in pv["template_text"]
    first = m.customers[0]
    members = agent._run_tool("find_loyalty_member", {"name": first.name.split()[0]}, m, run)
    assert first.id in run.customers  # members join the run's known customer ids
    assert members["members"][0]["member_id"] == first.id


def test_t20_ids_unseen_in_the_run_are_rejected():
    base = {"summary_tr": "x", "actions": []}
    prop = {**base, "actions": [{"type": "adjust_loyalty", "customer_id": "m-nope", "points": 50,
                                 "reason": "x", "label": "x"}]}
    errs = actions.validate_proposal(prop, set(), {}, set(), set(), {"m-1"}, {}, set(), {})
    assert any("find_customer" in e for e in errs)
    prop = {**base, "actions": [{"type": "ads_action", "campaign_id": "ad-nope", "action": "pause",
                                 "label": "x"}]}
    errs = actions.validate_proposal(prop, set(), {}, set(), set(), set(), {}, set(), {})
    assert any("get_ads_performance" in e for e in errs)
    prop = {**base, "actions": [{"type": "ads_action", "campaign_id": "ad-1", "action": "pause",
                                 "label": "x"}]}
    assert actions.validate_proposal(prop, set(), {}, set(), set(), set(), {}, {"ad-1"}, {}) == []


def _card(prop_actions, tool_log, seg_counts=None):
    ctx = MediaContext(instruction="x", instruction_source="text")
    return approval.render_card({"summary_tr": "özet", "actions": prop_actions}, ctx,
                                seg_counts or {}, approval.card_facts(tool_log))


def test_t20_card_campaign_preview_line():
    log = [{"tool": "preview_campaign", "result": {
        "segment_id": "seg-9", "template": TMPL,
        "template_text": "Merhaba ‹müşteri adı›.! indirim seni bekliyor.",
        "recipients": 23, "skipped": {"no_consent": 4, "other": 0}, "dry_run": True}}]
    card = _card([{"type": "send_campaign", "segment_id": "seg-9", "template": TMPL,
                   "variables": dict(VARS)}], log)
    assert "23 kişiye gidecek, 4 kişi izinsiz olduğu için atlanacak" in card
    assert "Merhaba ‹müşteri adı›." in card


def test_t20_card_loyalty_before_after_and_ads_budget():
    log = [{"tool": "find_loyalty_member", "result": {
        "members": [{"member_id": "m-1", "name": "Ayşe Yılmaz", "points": 450,
                     "tier": "gold"}]}}]
    card = _card([{"type": "adjust_loyalty", "customer_id": "m-1", "points": 200,
                   "reason": "doğum günü", "label": "x"}], log)
    assert "⭐ Puan: Ayşe Yılmaz 450→650 (doğum günü)" in card
    log = [{"tool": "get_ads_performance", "result": {
        "campaigns": [{"id": "ad-1", "name": "Yaz Elbiseleri", "daily_budget": 500.0}],
        "totals": {}}}]
    card = _card([{"type": "ads_action", "campaign_id": "ad-1", "action": "increase_budget",
                   "delta_pct": 10, "label": "x"}], log)
    assert "Yaz Elbiseleri" in card and "500→550 ₺" in card
