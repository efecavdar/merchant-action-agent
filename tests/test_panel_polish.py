"""Task 18: the Live-in-KEDD panel shows last-24h agent writes from the gateway's
GET /agent/report, and KEDD GETs time out in 8 s so a hung gateway falls back fast."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.channels import web as webchan
from app.config import settings
from app.tools.backend import KeddBackend, get_backend

FIX = Path(__file__).parent / "fixtures" / "kedd"
BASE = "https://kedd.example"

_REPORT = {"window_hours": 24, "total": 11, "by_capability": {
    "catalog.create": {"ok": 2, "error": 0},
    "catalog.update": {"ok": 1, "error": 1},
    "catalog.bulkPriceApply": {"ok": 1, "error": 0},
    "discounts.create": {"ok": 1, "error": 0},
    "appointments.create": {"ok": 1, "error": 0},
    "appointments.reschedule": {"ok": 1, "error": 0},
    # reads and out-of-scope writes must not reach the panel:
    "catalog.list": {"ok": 40, "error": 0},
    "segments.create": {"ok": 5, "error": 0},
    "ads.action": {"ok": 3, "error": 0},
}}


def _load(name: str):
    return json.loads((FIX / f"{name}.json").read_text())


class _Stub:
    """Serve one payload per (method, gateway path); record traffic incl. timeouts."""

    def __init__(self, routes: dict[tuple[str, str], object]):
        self.routes = routes
        self.calls: list[tuple] = []

    def _call(self, method, url, headers=None, data=None, timeout=None):
        self.calls.append((method, url, headers, json.loads(data) if data else None, timeout))
        route = self.routes[(method, url[len(BASE):].split("?")[0])]
        status, body = route if isinstance(route, tuple) else (200, route)
        return SimpleNamespace(json=lambda: body, status_code=status)

    def get(self, url, headers=None, timeout=None):
        return self._call("GET", url, headers, timeout=timeout)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._call("POST", url, headers, data, timeout)


def _routes(**overrides):
    routes = {
        ("GET", "/agent/profile"): _load("profile"),
        ("GET", "/agent/catalog"): _load("catalog"),
        ("GET", "/agent/schedule"): _load("schedule"),
        ("GET", "/agent/report"): _REPORT,
        ("POST", "/agent/segments"): _load("segments"),
    }
    routes.update(overrides)
    return routes


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
    cfg("kedd_demo_secret", "demo-s3cret")
    cfg("kedd_demo_phone", "+900000000001")
    return cfg


@pytest.fixture
def stub(monkeypatch):
    stub = _Stub(_routes())
    orig = KeddBackend.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self._s = stub

    monkeypatch.setattr(KeddBackend, "__init__", init)
    return stub


def test_t18_write_counts_keep_writes_only(demo_env, stub):
    counts = get_backend("kedd-demo").agent_write_counts()
    assert counts["window_hours"] == 24
    assert counts["counts"] == {
        "catalog.create": {"ok": 2, "error": 0},
        "catalog.update": {"ok": 1, "error": 1},
        "catalog.bulkPriceApply": {"ok": 1, "error": 0},
        "discounts.create": {"ok": 1, "error": 0},
        "appointments": {"ok": 2, "error": 0},  # create + reschedule merged
    }


def test_t18_write_counts_survive_a_shape_drift(demo_env, stub):
    stub.routes[("GET", "/agent/report")] = {"by_capability": {
        "catalog.create": {"ok": "3", "error": None},  # strings, nulls, missing keys
        "appointments.cancel": {},
        "unknown.future": {"ok": 9, "error": 0}}}
    counts = get_backend("kedd-demo").agent_write_counts()
    assert counts == {"window_hours": None,
                      "counts": {"catalog.create": {"ok": 0, "error": 0},
                                 "appointments": {"ok": 0, "error": 0}}}
    stub.routes[("GET", "/agent/report")] = {"window_hours": 24}
    assert get_backend("kedd-demo").agent_write_counts() == {"window_hours": 24, "counts": {}}


def test_t18_kedd_state_shows_write_counts(demo_env, stub, cfg, tmp_path):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    c = TestClient(webchan.create_app(), base_url="https://testserver")
    data = c.get("/api/kedd-state").json()
    assert data["state"] == "live"
    assert data["writes_window_hours"] == 24
    assert {"kind": "kedd", "capability": "catalog.create", "ok": 2, "error": 0} \
        in data["recent_writes"]
    assert {"kind": "kedd", "capability": "appointments", "ok": 2, "error": 0} \
        in data["recent_writes"]
    assert all("capability" in w for w in data["recent_writes"])
    blob = json.dumps(data)
    assert "catalog.list" not in blob and "ads.action" not in blob


def test_t18_mock_state_has_no_write_window(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    cfg("kedd_api_url", "")
    cfg("kedd_demo_secret", "")
    c = TestClient(webchan.create_app(), base_url="https://testserver")
    data = c.get("/api/kedd-state").json()
    assert data["state"] == "mock" and data["writes_window_hours"] is None
    assert all("capability" not in w for w in data["recent_writes"])


def test_t18_get_timeout_is_8s_post_stays_30s(demo_env, stub):
    kb = get_backend("kedd-demo")
    kb.store_profile()
    kb.find_customers(inactive_days=60)
    get_timeout = [c[4] for c in stub.calls if c[0] == "GET"]
    post_timeout = [c[4] for c in stub.calls if c[0] == "POST"]
    assert get_timeout and all(t == 8 for t in get_timeout)
    assert post_timeout and all(t == 30 for t in post_timeout)


def test_t18_write_labels_exist_in_both_languages():
    js = (webchan.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    i18n = (webchan.STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    # the panel's write vocabulary: every capability the server can send must be
    # labelled in both languages (unknown future ones render as the raw key)
    for cap in ("catalog.create", "catalog.update", "catalog.bulkPriceApply",
                "discounts.create", "appointments", "campaigns.send"):
        assert f'"kedd.w.{cap}"' in js, cap
        assert i18n.count(f'"kedd.w.{cap}"') == 2, cap  # once in en, once in tr
    assert i18n.count('"kedd.werr"') == 2
    assert "writes_window_hours" in js
