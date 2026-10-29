"""Task 17: public demo runs on the live KEDD demo business.

Scoped demo credential (secret + key id, never the main key), visible fallback to the
mock salon when KEDD is down, 3-approved-plans/IP/hour cap with a session flip, a
read-only Live-in-KEDD panel, and client-side refusal of demo writes. All offline: the
gateway is a routing stub, never the network.
"""

import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from app import agent, core, report
from app.channels import web as webchan
from app.config import settings
from app.tools.backend import KeddBackend, MockBackend, get_backend

FIX = Path(__file__).parent / "fixtures" / "kedd"
BASE = "https://kedd.example"
DEMO_SECRET = "demo-s3cret"
MAIN_SECRET = "main-s3cret-NEVER"
DEMO_PHONE = "+900000000001"

SRC_MODAL = (Path(__file__).resolve().parent.parent / "modal_app.py").read_text(encoding="utf-8")


def _load(name: str):
    return json.loads((FIX / f"{name}.json").read_text())


class _Stub:
    """Serve one payload per (method, gateway path); record traffic. Killable mid-test."""

    def __init__(self, routes: dict[tuple[str, str], object]):
        self.routes = routes
        self.calls: list[tuple] = []
        self.dead = False

    def _call(self, method, url, headers=None, data=None):
        self.calls.append((method, url, headers, json.loads(data) if data else None))
        if self.dead:
            raise requests.exceptions.ConnectionError("killed")
        route = self.routes[(method, url[len(BASE):].split("?")[0])]
        if isinstance(route, Exception):
            raise route
        status, body = route if isinstance(route, tuple) else (200, route)
        return SimpleNamespace(json=lambda: body, status_code=status)

    def get(self, url, headers=None, timeout=None):
        return self._call("GET", url, headers)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._call("POST", url, headers, data)


def _default_routes(**overrides):
    routes = {
        ("GET", "/agent/profile"): _load("profile"),
        ("GET", "/agent/catalog"): _load("catalog"),
        ("GET", "/agent/schedule"): _load("schedule"),
        ("GET", "/agent/report"): _load("report"),
        ("POST", "/agent/segments"): _load("segments"),
        ("POST", "/agent/catalog"): _load("catalog_create"),
    }
    routes.update(overrides)
    return routes


def _fake_plan(ctx, backend, max_revisions=2):
    seg = backend.find_customers(inactive_days=60)
    run = SimpleNamespace(turns=1, usage={}, segments={seg["segment_id"]}, answer=None,
                          tool_log=[{"tool": "find_customers", "args": {}, "result": seg}])
    run.proposal = {"summary_tr": "Fular ekleniyor, 1 kampanya.", "actions": [
        {"type": "add_catalog_item", "item_type": "product", "ref": "n1",
         "name": "İpek Fular", "price": 850},
        {"type": "send_campaign", "segment_id": seg["segment_id"], "message": "Fular 850 TL!"}]}
    return run


def _k1_2(secret: str, ts: str, method: str, target: str, phone: str, body: str) -> str:
    msg = f"{ts}.{method}.{target}.{phone}..{body}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def cfg():
    saved = {}

    def _set(name, value):
        saved.setdefault(name, getattr(settings, name))
        object.__setattr__(settings, name, value)

    yield _set
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


@pytest.fixture
def demo_env(cfg):
    cfg("kedd_api_url", BASE)
    cfg("kedd_demo_secret", DEMO_SECRET)
    cfg("kedd_demo_phone", DEMO_PHONE)
    cfg("kedd_agent_secret", MAIN_SECRET)
    return cfg


@pytest.fixture
def kedd_stub(monkeypatch):
    stub = _Stub(_default_routes())
    orig = KeddBackend.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self._s = stub

    monkeypatch.setattr(KeddBackend, "__init__", init)
    return stub


@pytest.fixture
def pubapp(cfg, tmp_path, demo_env):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    cfg("public_demo_daily_cap", 400)
    return webchan.create_app()  # https clients so the Secure cookie round-trips


def _client(app):
    return TestClient(app, base_url="https://testserver")


# --------------------------------------------------------------------------- #
# item 2: scoped demo credential — the main key must be unreachable in public
# --------------------------------------------------------------------------- #

def test_t17_signer_sends_demo_key_id_and_signs_with_demo_secret(kedd_stub, demo_env):
    kb = get_backend("kedd-demo")
    assert isinstance(kb, KeddBackend)
    kb.store_profile()
    assert kedd_stub.calls, "expected a gateway call"
    _m, url, headers, _b = kedd_stub.calls[-1]
    assert headers["X-Agent-Key-Id"] == "demo"
    assert headers["X-Agent-Phone"] == DEMO_PHONE
    target = url[len(BASE):]
    expect = _k1_2(DEMO_SECRET, headers["X-Agent-Timestamp"], "GET", target, DEMO_PHONE, "")
    assert headers["X-Agent-Signature"] == expect
    assert headers["X-Agent-Signature"] != _k1_2(
        MAIN_SECRET, headers["X-Agent-Timestamp"], "GET", target, DEMO_PHONE, "")


def test_t17_signer_defaults_to_main_key_id(kedd_stub, demo_env):
    kb = KeddBackend(BASE, MAIN_SECRET, "+905321")
    kb._s = kedd_stub
    kb.store_profile()
    assert kedd_stub.calls[-1][2]["X-Agent-Key-Id"] == "main"


def test_t17_get_backend_demo_needs_all_three_settings(cfg):
    cfg("kedd_api_url", BASE)
    cfg("kedd_demo_secret", "")
    cfg("kedd_demo_phone", DEMO_PHONE)
    with pytest.raises(RuntimeError, match="KEDD_DEMO_SECRET"):
        get_backend("kedd-demo")


def test_t17_demo_unpickle_restores_demo_secret_not_main(cfg, tmp_path):
    import pickle

    cfg("kedd_api_url", BASE)
    cfg("kedd_demo_secret", DEMO_SECRET)
    cfg("kedd_demo_phone", DEMO_PHONE)
    cfg("kedd_agent_secret", MAIN_SECRET)
    kb = get_backend("kedd-demo")
    kb2 = pickle.loads(pickle.dumps(kb))
    assert kb2._key_id == "demo"
    kb2._s = _Stub(_default_routes())
    kb2.store_profile()
    _m, url, headers, _b = kb2._s.calls[-1]
    assert headers["X-Agent-Key-Id"] == "demo"
    assert headers["X-Agent-Signature"] == _k1_2(
        DEMO_SECRET, headers["X-Agent-Timestamp"], "GET", url[len(BASE):], DEMO_PHONE, "")


def test_t17_gateway_failures_are_kedd_errors_not_plain_valueerrors(demo_env):
    from app.tools.backend import KeddError

    assert issubclass(KeddError, ValueError)  # existing `raises ValueError` stays green
    kb = KeddBackend(BASE, DEMO_SECRET, DEMO_PHONE, key_id="demo")
    kb._s = _Stub({("GET", "/agent/profile"): (503, {"error": "disabled", "message": "Kapalı."})})
    with pytest.raises(KeddError, match="Kapalı"):
        kb.store_profile()
    kb._s = _Stub({("GET", "/agent/profile"): requests.exceptions.ConnectionError("down")})
    with pytest.raises(KeddError, match="ulaşılamadı"):
        kb.store_profile()


# --------------------------------------------------------------------------- #
# item 5: demo writes are refused client-side (gateway policy is the 2nd lock)
# --------------------------------------------------------------------------- #

def test_t17_demo_send_campaign_refused_without_touching_network(kedd_stub, demo_env):
    kb = get_backend("kedd-demo")
    with pytest.raises(ValueError, match="önizleme"):
        kb.send_campaign("Ad", "seg-1", "mesaj")
    assert kedd_stub.calls == []


def test_t17_demo_publish_refused_even_as_dry_run(kedd_stub, demo_env, tmp_path):
    kb = get_backend("kedd-demo")
    img = tmp_path / "v.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    for dry in (False, True):
        with pytest.raises(ValueError, match="kapalı"):
            kb.publish_social_post("cap", str(img), ["instagram"], dry_run=dry)
    assert kedd_stub.calls == []


def test_t17_main_key_dry_run_still_returns_preview(kedd_stub, demo_env, tmp_path):
    kb = KeddBackend(BASE, MAIN_SECRET, "+905321")
    kb._s = kedd_stub
    img = tmp_path / "v.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    res = kb.publish_social_post("cap", str(img), ["instagram"], dry_run=True)
    assert res["status"] == "preview" and kedd_stub.calls == []


# --------------------------------------------------------------------------- #
# items 2+4: public sessions default to kedd-demo; /kedd stays refused
# --------------------------------------------------------------------------- #

def test_t17_public_default_business_is_kedd_demo(pubapp, kedd_stub):
    c = _client(pubapp)
    r = c.get("/")
    assert r.status_code == 200
    assert 'id="badge" class="badge kedd">KEDD demo<' in r.text
    assert "Örnek Kuaför" in r.text  # the header name comes from the live profile read
    _m, _u, headers, _b = kedd_stub.calls[-1]
    assert headers["X-Agent-Key-Id"] == "demo"


def test_t17_mock_default_unchanged_without_demo_creds(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    cfg("kedd_api_url", "")
    cfg("kedd_demo_secret", "")
    c = TestClient(webchan.create_app(), base_url="https://testserver")
    r = c.get("/")
    assert 'id="badge" class="badge demo">demo<' in r.text


def test_t17_kedd_command_still_refused_in_public_with_demo_creds(pubapp, kedd_stub, monkeypatch):
    monkeypatch.setattr(webchan.core, "handle", lambda *a, **k: [core.Reply("ok")])
    c = _client(pubapp)
    r = c.post("/api/message", data={"text": "/kedd"})
    assert "yalnız örnek işletmelerle" in r.json()["replies"][0]["text"]
    assert r.json()["business"] == "kedd-demo"  # default, not the main-key business


# --------------------------------------------------------------------------- #
# item 3: KEDD down → chat keeps state and retries, panel falls back
# --------------------------------------------------------------------------- #

def test_t17_kedd_down_mid_conversation_keeps_state_and_retries(pubapp, kedd_stub, monkeypatch):
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep: "Hallettim.")
    c = _client(pubapp)
    r = c.post("/api/message", data={"text": "Fular ekle 850"})
    assert "onayına sunuyorum" in r.json()["replies"][-1]["text"]

    kedd_stub.dead = True  # gateway dies mid-conversation
    r = c.post("/api/message", data={"text": "Başka bir fular daha ekle 900"})
    body = r.json()
    assert body["business"] == "kedd-demo"  # state kept, no silent flip to mock
    assert "KEDD'ye şu an ulaşamıyorum" in body["replies"][0]["text"]
    assert "onayına sunuyorum" not in "".join(x["text"] for x in body["replies"])

    kedd_stub.dead = False  # recovers: the very next message retries KEDD
    r = c.post("/api/message", data={"text": "Fular ekle 850"})
    assert "onayına sunuyorum" in r.json()["replies"][-1]["text"]


def test_t17_brief_reports_kedd_down_instead_of_generic_failure(pubapp, kedd_stub):
    kedd_stub.dead = True
    c = _client(pubapp)
    r = c.post("/api/brief")
    assert "KEDD'ye şu an ulaşamıyorum" in r.json()["replies"][0]["text"]


# --------------------------------------------------------------------------- #
# item 4: 3 approved plans per IP per hour → flip to mock salon with a banner
# --------------------------------------------------------------------------- #

def _approve_cycle(c):
    r = c.post("/api/message", data={"text": "Fular ekle 850"})
    assert "onayına sunuyorum" in r.json()["replies"][-1]["text"]
    return c.post("/api/message", data={"text": "evet"})


def test_t17_cap_flips_session_to_mock_salon_on_fourth_approval(pubapp, kedd_stub, monkeypatch):
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep: "Hallettim.")
    c = _client(pubapp)
    for _ in range(3):
        r = _approve_cycle(c)
        assert "demo kotan doldu" not in r.json()["replies"][0]["text"]
    catalog_posts = [x for x in kedd_stub.calls if x[0] == "POST" and x[1].endswith("/agent/catalog")]
    assert len(catalog_posts) == 3  # the first three approvals really executed

    r = _approve_cycle(c)
    body = r.json()
    assert "demo kotan doldu" in body["replies"][0]["text"]
    assert body["business"] == "salon"
    catalog_posts = [x for x in kedd_stub.calls if x[0] == "POST" and x[1].endswith("/agent/catalog")]
    assert len(catalog_posts) == 3  # the fourth plan never ran


def test_t17_cap_is_per_ip(pubapp, kedd_stub, monkeypatch):
    monkeypatch.setattr(agent, "plan", _fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep: "Hallettim.")
    a = _client(pubapp)
    b = _client(pubapp)
    b.headers["x-forwarded-for"] = "9.9.9.9"
    for _ in range(3):
        _approve_cycle(a)
    r = _approve_cycle(b)  # another IP still has its own quota
    assert "demo kotan doldu" not in r.json()["replies"][0]["text"]
    assert r.json()["business"] == "kedd-demo"
    catalog_posts = [x for x in kedd_stub.calls if x[0] == "POST" and x[1].endswith("/agent/catalog")]
    assert len(catalog_posts) == 4


# --------------------------------------------------------------------------- #
# items 3+7: GET /api/kedd-state — cached, PII-free, falls back with the error
# --------------------------------------------------------------------------- #

def test_t17_kedd_state_live_shape_and_10s_cache(pubapp, kedd_stub):
    c = _client(pubapp)
    r = c.get("/api/kedd-state")
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "live" and data["business"] == "kedd-demo"
    assert data["error"] is None
    assert data["catalog_sample"] and len(data["catalog_sample"]) <= 5
    assert set(data["catalog_sample"][0]) == {"name", "price"}
    assert data["appointments_today"]["count"] >= 1
    # task 18: live writes come from GET /agent/report (reads like "profile" excluded)
    assert data["writes_window_hours"] == 24
    assert data["recent_writes"] == [
        {"kind": "kedd", "capability": "catalog.create", "ok": 1, "error": 1}]
    n_calls = len(kedd_stub.calls)
    assert n_calls > 0
    c.get("/api/kedd-state")  # served from the 10 s cache: no new gateway traffic
    assert len(kedd_stub.calls) == n_calls
    c.get("/api/kedd-state", params={"fresh": "1"})  # the retry button bypasses the cache
    assert len(kedd_stub.calls) > n_calls


def test_t17_kedd_state_falls_back_to_mock_salon_with_error(pubapp, kedd_stub):
    c = _client(pubapp)
    assert c.get("/api/kedd-state").json()["state"] == "live"
    kedd_stub.routes[("GET", "/agent/catalog")] = (503, {"error": "disabled", "message": "Kapalı."})
    data = c.get("/api/kedd-state", params={"fresh": "1"}).json()
    assert data["state"] == "fallback" and data["business"] == "salon"
    assert data["error"] and "Kapalı" in data["error"]
    assert data["catalog_sample"], "fallback still shows mock salon data"


def test_t17_kedd_state_recovers_on_retry(pubapp, kedd_stub):
    c = _client(pubapp)
    kedd_stub.dead = True
    assert c.get("/api/kedd-state", params={"fresh": "1"}).json()["state"] == "fallback"
    kedd_stub.dead = False
    data = c.get("/api/kedd-state", params={"fresh": "1"}).json()
    assert data["state"] == "live" and data["error"] is None


def test_t17_kedd_state_strips_customer_pii(pubapp, kedd_stub):
    smuggled = dict(_load("profile"))
    smuggled["business"] = dict(smuggled["business"], contact_phone="+905551112233")
    kedd_stub.routes[("GET", "/agent/profile")] = smuggled
    c = _client(pubapp)
    data = c.get("/api/kedd-state", params={"fresh": "1"}).json()
    blob = json.dumps(data, ensure_ascii=False)
    assert "Ayşe Y." not in blob and "Elif K." not in blob  # schedule fixture customers
    assert "+905551112233" not in blob  # smuggled into the profile fixture
    for item in data["appointments_today"]["items"]:
        assert "customer" not in item and "phone" not in item


def test_t17_kedd_state_mock_session_reports_mock(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    cfg("kedd_api_url", "")
    cfg("kedd_demo_secret", "")
    c = TestClient(webchan.create_app(), base_url="https://testserver")
    data = c.get("/api/kedd-state").json()
    assert data["state"] == "mock" and data["business"] == "butik"
    assert data["catalog_sample"] and data["error"] is None


def test_t17_kedd_state_burns_no_quota(pubapp, kedd_stub, cfg):
    cfg("public_demo_daily_cap", 0)  # every counted call is refused from here on
    c = _client(pubapp)
    r = c.post("/api/message", data={"text": "iş"})
    assert "kota" in r.json()["replies"][0]["text"].lower() or \
        "quota" in r.json()["replies"][0]["text"].lower()
    assert c.get("/api/kedd-state").status_code == 200


# --------------------------------------------------------------------------- #
# item 7: the panel — markup, polling, i18n parity
# --------------------------------------------------------------------------- #

def test_t17_panel_markup_and_polling():
    html = (webchan.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (webchan.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for hook in ("keddLive", "keddPill", "keddRows", "keddRetry", "keddError"):
        assert hook in html, hook
    assert "/api/kedd-state" in js and "fresh" in js


def test_t17_panel_i18n_keys_exist_in_both_languages():
    html = (webchan.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (webchan.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    i18n = (webchan.STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    import re

    used = set(re.findall(r'data-i18n="(kedd\.[^"]+)"', html)) | set(
        re.findall(r'\bt\("(kedd\.[^"]+)"\)', js))
    assert used, "panel must route its labels through i18n.js"
    for key in sorted(used):
        assert i18n.count(f'"{key}"') == 2, key  # once in en, once in tr


# --------------------------------------------------------------------------- #
# modal + config plumbing
# --------------------------------------------------------------------------- #

def test_t17_demo_secret_carries_demo_credential_only():
    assert "KEDD_DEMO_SECRET" in SRC_MODAL and "KEDD_DEMO_PHONE" in SRC_MODAL
    assert "KEDD_API_URL" in SRC_MODAL
    at = SRC_MODAL.index("def demo():")
    deco = SRC_MODAL[SRC_MODAL.rindex("@app.function", 0, at):at]
    assert "secrets=[demo_secret]" in deco
    assert SRC_MODAL.count("is_local()") >= 1 and "Secret.from_dict({})" in SRC_MODAL


def test_t17_public_guard_never_leaves_main_key_backend(pubapp, kedd_stub, monkeypatch):
    # Even if a main-key KeddBackend somehow lands on a public session, the guard
    # neutralizes it before the next request uses it: request 2 signs demo-only.
    planted = []

    def handle(session, kind, text="", media_path="", notify=None):
        if not planted:
            session.backend = KeddBackend(BASE, MAIN_SECRET, "+905321")  # main key smuggled in
            planted.append(True)
        return [core.Reply("ok")]

    monkeypatch.setattr(webchan.core, "handle", handle)
    c = _client(pubapp)
    c.post("/api/message", data={"text": "bir"})
    kedd_stub.calls.clear()
    r = c.post("/api/message", data={"text": "iki"})  # guard runs before this handle
    assert r.json()["business"] == "kedd-demo"
    key_ids = [x[2]["X-Agent-Key-Id"] for x in kedd_stub.calls if x[2]]
    assert key_ids and set(key_ids) == {"demo"}
