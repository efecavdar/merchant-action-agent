"""Task 21: demo-key gateway smoke script, tested against fixture stubs."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import smoke_kedd
from app.config import settings
from app.tools.backend import KeddBackend, get_backend

FIX = Path(__file__).parent / "fixtures" / "kedd"
BASE = "https://kedd.example"


def _load(name: str):
    return json.loads((FIX / f"{name}.json").read_text())


class _Stub:
    def __init__(self, routes: dict[tuple[str, str], object]):
        self.routes = routes
        self.calls: list[tuple] = []

    def _call(self, method, url, headers=None, data=None):
        self.calls.append((method, url, headers, json.loads(data) if data else None))
        route = self.routes[(method, url[len(BASE):].split("?")[0])]
        status, body = route if isinstance(route, tuple) else (200, route)
        return SimpleNamespace(json=lambda: body, status_code=status)

    def get(self, url, headers=None, timeout=None):
        return self._call("GET", url, headers)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._call("POST", url, headers, data)


def _routes(**overrides):
    routes = {
        ("GET", "/agent/profile"): _load("profile"),
        ("GET", "/agent/catalog"): _load("catalog"),
        ("GET", "/agent/schedule"): _load("schedule"),
        ("GET", "/agent/customers"): _load("customers"),
        ("GET", "/agent/daily-summary"): _load("daily_summary"),
        ("POST", "/agent/segments"): _load("segments"),
        ("GET", "/agent/campaign-templates"): _load("campaign_templates"),
        ("GET", "/agent/loyalty/members"): _load("loyalty_members"),
        ("GET", "/agent/ads/performance"): _load("ads_performance"),
        ("GET", "/agent/report"): _load("report"),
        ("POST", "/agent/campaigns"): _load("campaigns_dryrun"),
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
def demo_env(cfg, monkeypatch):
    for k, v in {"KEDD_API_URL": BASE, "KEDD_DEMO_SECRET": "SECRET-XYZ",
                 "KEDD_DEMO_PHONE": "+900000000001"}.items():
        monkeypatch.setenv(k, v)
    cfg("kedd_api_url", BASE)
    cfg("kedd_demo_secret", "SECRET-XYZ")
    cfg("kedd_demo_phone", "+900000000001")


@pytest.fixture
def stub(monkeypatch):
    stub = _Stub(_routes())
    orig = KeddBackend.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self._s = stub

    monkeypatch.setattr(KeddBackend, "__init__", init)
    return stub


def test_t21_rows_cover_every_read_plus_dryrun(demo_env, stub):
    rows = smoke_kedd.run_smoke(get_backend("kedd-demo"), "2026-10-29")
    assert [(r["method"], r["path"]) for r in rows] == [
        ("GET", "/agent/profile"), ("GET", "/agent/catalog"), ("GET", "/agent/schedule"),
        ("GET", "/agent/customers"), ("GET", "/agent/daily-summary"),
        ("POST", "/agent/segments"), ("GET", "/agent/campaign-templates"),
        ("GET", "/agent/loyalty/members"), ("GET", "/agent/ads/performance"),
        ("GET", "/agent/report"), ("POST", "/agent/campaigns")]
    assert all(r["status"] == 200 for r in rows)
    by_path = {(r["method"], r["path"]): r for r in rows}
    assert by_path[("GET", "/agent/catalog")]["rows"] == len(_load("catalog")["items"])
    assert by_path[("POST", "/agent/segments")]["rows"] == 12
    assert by_path[("GET", "/agent/loyalty/members")]["rows"] == 2
    assert by_path[("POST", "/agent/campaigns")]["rows"] == 23
    assert all(isinstance(r["ms"], int) and r["ms"] >= 0 for r in rows)


def test_t21_only_dryrun_writes(demo_env, stub):
    smoke_kedd.run_smoke(get_backend("kedd-demo"), "2026-10-29")
    posts = [c for c in stub.calls if c[0] == "POST"]
    assert {c[1] for c in posts} <= {f"{BASE}/agent/segments", f"{BASE}/agent/campaigns"}
    for _m, url, _h, body in posts:
        if url == f"{BASE}/agent/campaigns":
            assert body["dry_run"] is True


def test_t21_loyalty_404_is_a_row_not_a_crash(demo_env, stub, capsys):
    stub.routes[("GET", "/agent/loyalty/members")] = (404, {"error": "not_found"})
    code = smoke_kedd.main([])
    out = capsys.readouterr().out
    assert code == 1
    assert "loyalty/members" in out and "404" in out
    assert out.count("\n") >= 12  # header + all 11 rows still printed


def test_t21_refuses_without_demo_secret(demo_env, monkeypatch, capsys):
    monkeypatch.delenv("KEDD_DEMO_SECRET")
    assert smoke_kedd.main([]) == 1
    assert "refusing" in capsys.readouterr().err


def test_t21_output_carries_no_secrets_names_or_phones(demo_env, stub, capsys):
    assert smoke_kedd.main([]) == 0
    out = capsys.readouterr().out
    assert "SECRET-XYZ" not in out and "+900000000001" not in out and "+90" not in out
    for name in ("Ayşe", "Elif", "Yılmaz", "Kuaför"):
        assert name not in out
