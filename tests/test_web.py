"""Web UI channel: sessions, uploads through core.handle, media guard, per-session lock."""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import core
from app.channels import web as webchan
from app.config import settings
from app.tools.backend import KeddBackend


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
def client(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    return TestClient(webchan.create_app())


def test_index_sets_session_cookie(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Çırak" in r.text
    assert "cirak_sid" in (r.cookies or client.cookies)
    assert "Butik" in r.text or "Nar" in r.text or "business" in r.text.lower()
    assert 'id="badge" class="badge demo">demo<' in r.text


def test_message_text_goes_through_core_handle(client, monkeypatch):
    calls = []

    def fake_handle(session, kind, text="", media_path="", notify=None):
        calls.append((kind, text, media_path))
        if notify:
            notify("⏳ Bakıyorum…")
        return [core.Reply("onay kartı *test*")]

    monkeypatch.setattr(webchan.core, "handle", fake_handle)
    r = client.post("/api/message", data={"text": "Fular ekle 850"})
    assert r.status_code == 200
    body = r.json()
    assert body["replies"][0]["text"] == "onay kartı *test*"
    assert body["status"] == ["⏳ Bakıyorum…"]
    assert calls and calls[-1][0] == "text" and "Fular" in calls[-1][1]
    assert "cirak_sid" in client.cookies


def test_message_images_then_instruction(client, monkeypatch, tmp_path):
    calls = []

    def fake_handle(session, kind, text="", media_path="", notify=None):
        calls.append((kind, text, media_path))
        return [core.Reply(f"{kind} ok")]

    monkeypatch.setattr(webchan.core, "handle", fake_handle)
    files = [("images[]", ("a.jpg", b"fake1", "image/jpeg")), ("images[]", ("b.png", b"fake2", "image/png"))]
    r = client.post("/api/message", data={"text": "bunları ekle"}, files=files)
    assert r.status_code == 200
    kinds = [c[0] for c in calls]
    assert kinds == ["image", "image", "text"]
    assert all(c[2] and tmp_path.as_posix() in c[2] for c in calls[:2])
    assert len(r.json()["replies"]) == 3


def test_message_image_url_and_media_serving(client, monkeypatch, tmp_path):
    client.get("/")
    sid = client.cookies.get("cirak_sid")
    assert sid
    visual = tmp_path / "web" / sid / "campaign_visual.png"
    visual.parent.mkdir(parents=True, exist_ok=True)
    visual.write_bytes(PNG_BYTES)

    def fake_handle(session, kind, text="", media_path="", notify=None):
        return [core.Reply("Hallettim.", image_path=str(visual))]

    monkeypatch.setattr(webchan.core, "handle", fake_handle)
    r = client.post("/api/message", data={"text": "evet"})
    url = r.json()["replies"][0]["image_url"]
    assert url and url.startswith(f"/api/media/web/{sid}/")
    got = client.get(url)
    assert got.status_code == 200 and got.content == PNG_BYTES
    assert got.headers["x-content-type-options"] == "nosniff"


def test_media_rejects_traversal(client, tmp_path):
    client.get("/")
    sid = client.cookies.get("cirak_sid")
    assert sid
    ok = tmp_path / "web" / sid / "ok.png"
    ok.parent.mkdir(parents=True, exist_ok=True)
    ok.write_bytes(PNG_BYTES)
    assert client.get(f"/api/media/web/{sid}/ok.png").status_code == 200
    assert client.get("/api/media/../secret").status_code in (400, 404)
    assert client.get("/api/media/%2e%2e/secret").status_code in (400, 404)
    assert client.get(f"/api/media/web/{sid}/missing.png").status_code == 404
    assert client.get("/api/media/ok.png").status_code == 404  # outside your session dir


def test_reset_calls_sifirla(client, monkeypatch):
    seen = []

    def fake_handle(session, kind, text="", media_path="", notify=None):
        seen.append((kind, text))
        return [core.Reply("Sıfırladım.")]

    monkeypatch.setattr(webchan.core, "handle", fake_handle)
    r = client.post("/api/reset")
    assert r.status_code == 200 and seen == [("text", "/sifirla")]


def test_second_request_waits_for_first(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    app_obj = webchan.create_app()
    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def fake_handle(session, kind, text="", media_path="", notify=None):
        if text == "first":
            entered.set()
            assert release.wait(timeout=10)
            order.append("first")
        else:
            order.append("second")
        return [core.Reply("ok")]

    monkeypatch.setattr(webchan.core, "handle", fake_handle)
    sid = TestClient(app_obj).get("/").cookies.get("cirak_sid")
    assert sid
    c1, c2 = TestClient(app_obj), TestClient(app_obj)
    c1.cookies.set("cirak_sid", sid)
    c2.cookies.set("cirak_sid", sid)
    results: dict[str, object] = {}

    def _post(client, name, text):
        results[name] = client.post("/api/message", data={"text": text})

    t1 = threading.Thread(target=_post, args=(c1, "a", "first"))
    t1.start()
    assert entered.wait(timeout=10)
    t2 = threading.Thread(target=_post, args=(c2, "b", "second"))
    t2.start()
    time.sleep(0.3)
    assert t2.is_alive()  # second waits while the first holds the session lock
    release.set()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert order == ["first", "second"]
    assert results["a"].status_code == 200 and results["b"].status_code == 200  # type: ignore[union-attr]


def test_responses_carry_business_fields(client, monkeypatch):
    monkeypatch.setattr(webchan.core, "handle", lambda *a, **k: [core.Reply("ok")])
    monkeypatch.setattr(webchan, "build_morning_brief", lambda session: [core.Reply("brief")])
    msg = client.post("/api/message", data={"text": "selam"}).json()
    assert (msg["business"], msg["business_name"]) == ("butik", "Nar Butik")
    brief = client.post("/api/brief").json()
    assert (brief["business"], brief["business_name"]) == ("butik", "Nar Butik")
    reset = client.post("/api/reset").json()
    assert (reset["business"], reset["business_name"]) == ("butik", "Nar Butik")


def test_different_sessions_do_not_block_each_other(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    app_obj = webchan.create_app()
    entered = threading.Event()
    release = threading.Event()

    def fake_handle(session, kind, text="", media_path="", notify=None):
        if text == "first":
            entered.set()
            assert release.wait(timeout=10)
        return [core.Reply("ok")]

    monkeypatch.setattr(webchan.core, "handle", fake_handle)
    c1, c2 = TestClient(app_obj), TestClient(app_obj)
    c1.get("/")                                    # two distinct sessions…
    c2.get("/")
    assert c1.cookies.get("cirak_sid") != c2.cookies.get("cirak_sid")
    results: dict[str, object] = {}

    def _post(client, name, text):
        results[name] = client.post("/api/message", data={"text": text})

    t1 = threading.Thread(target=_post, args=(c1, "a", "first"))
    t1.start()
    assert entered.wait(timeout=10)
    t2 = threading.Thread(target=_post, args=(c2, "b", "second"))
    t2.start()
    t2.join(timeout=10)
    assert not t2.is_alive()                       # …so the second is not serialized behind the first
    release.set()
    t1.join(timeout=10)
    assert results["a"].status_code == 200 and results["b"].status_code == 200  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# task 13: public demo (mock only, rate-limited, bannered)
# --------------------------------------------------------------------------- #

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture
def pub(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    cfg("public_demo_daily_cap", 400)
    monkeypatch.setattr(webchan.core, "handle", lambda *a, **k: [core.Reply("ok")])
    # https so the Secure cookie (L7) is sent back on later requests
    return TestClient(webchan.create_app(), base_url="https://testserver")


def test_public_kedd_refused_even_with_env(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    cfg("kedd_api_url", "https://kedd.example")
    cfg("kedd_agent_secret", "s3cret")
    built = []
    orig_init = KeddBackend.__init__

    def spy_init(self, *a, **k):
        built.append(True)
        return orig_init(self, *a, **k)

    monkeypatch.setattr(KeddBackend, "__init__", spy_init)
    c = TestClient(webchan.create_app())
    r = c.post("/api/message", data={"text": "/kedd"})
    assert r.status_code == 200
    assert "yalnız örnek işletmelerle" in r.json()["replies"][0]["text"]
    assert built == [] and r.json()["business"] != "kedd"


def test_public_per_ip_limit_no_model_call(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    cfg("public_demo_daily_cap", 400)
    calls = []
    monkeypatch.setattr(webchan.core, "handle",
                        lambda *a, **k: calls.append(1) or [core.Reply("ok")])
    c = TestClient(webchan.create_app(), base_url="https://testserver")
    for i in range(webchan.IP_HOUR_MAX):
        r = c.post("/api/message", data={"text": f"iş {i}"})
        assert r.status_code == 200 and "rate limit" not in r.json()["replies"][0]["text"]
    n_before = len(calls)
    r = c.post("/api/message", data={"text": "bir tane daha"})
    assert "rate limit" in r.json()["replies"][0]["text"].lower()
    assert len(calls) == n_before  # limited request made no model call
    # commands and greetings stay free
    assert c.post("/api/message", data={"text": "/butik"}).json()["replies"]


def test_public_global_limit_no_model_call(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    cfg("public_demo_daily_cap", 2)
    calls = []
    monkeypatch.setattr(webchan.core, "handle",
                        lambda *a, **k: calls.append(1) or [core.Reply("ok")])
    app_obj = webchan.create_app()
    a, b, d = TestClient(app_obj), TestClient(app_obj), TestClient(app_obj)
    assert "quota" not in a.post("/api/message", data={"text": "iş bir"},
                                 headers={"x-forwarded-for": "10.0.0.1"}).json()["replies"][0]["text"].lower()
    assert "quota" not in b.post("/api/message", data={"text": "iş iki"},
                                 headers={"x-forwarded-for": "10.0.0.2"}).json()["replies"][0]["text"].lower()
    n_before = len(calls)
    r = d.post("/api/message", data={"text": "iş üç"}, headers={"x-forwarded-for": "10.0.0.3"})
    assert "quota" in r.json()["replies"][0]["text"].lower()
    assert len(calls) == n_before


def test_public_upload_magic_bytes(pub, monkeypatch):
    calls = []
    monkeypatch.setattr(webchan.core, "handle",
                        lambda *a, **k: calls.append(1) or [core.Reply("ok")])
    bad = pub.post("/api/message", files=[("images[]", ("x.jpg", b"not-an-image", "image/jpeg"))])
    assert "jpg veya png" in bad.json()["replies"][0]["text"] and not calls
    many = pub.post("/api/message", files=[("images[]", (f"{i}.png", PNG_BYTES, "image/png"))
                                           for i in range(5)])
    assert "4 fotoğraf" in many.json()["replies"][0]["text"]
    big = pub.post("/api/message", files=[("images[]", ("b.png", PNG_BYTES + b"x" * (6 * 1024 * 1024),
                                                       "image/png"))])
    assert "5 MB" in big.json()["replies"][0]["text"]
    ok = pub.post("/api/message", files=[("images[]", ("a.png", PNG_BYTES, "image/png"))])
    assert ok.json()["replies"][0]["text"] == "ok" and calls


def test_public_session_expiry_and_eviction(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    monkeypatch.setattr(webchan.core, "handle", lambda *a, **k: [core.Reply("ok")])
    # expiry, unit level: idle sessions are dropped by the sweep
    sessions: dict = {"old": core.Session(sender="web-old")}
    last_seen = {"old": 1_000.0}
    locks: dict = {"old": threading.Lock()}
    webchan._drop_expired(sessions, last_seen, locks, 1_000.0 + webchan.SESSION_IDLE_S + 1)
    assert sessions == {} and last_seen == {}
    # eviction, behavioural: the app survives a full house and still answers
    monkeypatch.setattr(webchan, "MAX_SESSIONS", 3)
    monkeypatch.setattr(webchan, "SESSION_CREATE_HOUR_MAX", 10_000)
    app_obj = webchan.create_app()
    sids = [TestClient(app_obj).get("/").cookies.get("cirak_sid") for _ in range(4)]
    assert len(set(sids)) == 4  # oldest evicted, newest admitted
    c = TestClient(app_obj)
    c.cookies.set("cirak_sid", sids[-1])  # type: ignore[arg-type]
    assert c.post("/api/message", data={"text": "selam"}).status_code == 200


def test_public_banner_only_in_public_mode(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    cfg("public_demo", False)
    assert "demobanner" not in TestClient(webchan.create_app()).get("/").text
    cfg("public_demo", True)
    assert "demobanner" in TestClient(webchan.create_app()).get("/").text
    assert "Nothing is sent to real customers" in TestClient(webchan.create_app()).get("/").text


# --------------------------------------------------------------------------- #
# review hardening: H1/M4-M7/L6-L7 (tests first)
# --------------------------------------------------------------------------- #

def test_h1_demo_secret_never_reads_env_in_container():
    src = (Path(__file__).resolve().parent.parent / "modal_app.py").read_text(encoding="utf-8")
    assert "is_local()" in src
    assert "Secret.from_dict({})" in src
    guard = src.index("is_local()")
    read = src.index('with_name(".env")')
    assert read > guard  # the .env parse lives under the is_local() guard


def test_m4_content_length_rejected_before_parsing(pub, monkeypatch):
    calls = []
    monkeypatch.setattr(webchan.core, "handle",
                        lambda *a, **k: calls.append(1) or [core.Reply("ok")])
    r = pub.post("/api/message", content=b"x=1",
                 headers={"content-type": "application/x-www-form-urlencoded",
                          "content-length": str(23 * 1024 * 1024)})
    assert r.status_code == 413 and not calls


def test_m4_form_field_file_caps(pub):
    files = [(f"f{i}", (f"{i}.png", PNG_BYTES, "image/png")) for i in range(6)]
    assert pub.post("/api/message", files=files).status_code == 413
    data = {f"k{i}": "v" for i in range(6)}
    assert pub.post("/api/message", data=data).status_code == 413


def test_m4_text_capped_at_1000(pub, monkeypatch):
    calls = []
    monkeypatch.setattr(webchan.core, "handle",
                        lambda *a, **k: calls.append(1) or [core.Reply("ok")])
    r = pub.post("/api/message", data={"text": "x" * 1001})
    assert r.status_code == 200 and not calls
    assert "1000" in r.json()["replies"][0]["text"] or "uzun" in r.json()["replies"][0]["text"]
    assert webchan.MAX_TEXT_CHARS == 1000


def test_m4_image_pixel_limit():
    from PIL import Image
    assert Image.MAX_IMAGE_PIXELS == 40_000_000


def test_m5_client_ip_uses_proxy_appended_hop():
    from starlette.requests import Request
    scope = {"type": "http", "headers": [(b"x-forwarded-for", b"spoofed, 9.9.9.9")],
             "client": ("1.2.3.4", 123)}
    assert webchan._client_ip(Request(scope)) == "9.9.9.9"


def test_m5_per_session_cap_survives_ip_rotation(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    cfg("public_demo_daily_cap", 10_000)
    calls = []
    monkeypatch.setattr(webchan.core, "handle",
                        lambda *a, **k: calls.append(1) or [core.Reply("ok")])
    c = TestClient(webchan.create_app(), base_url="https://testserver")
    c.get("/")
    for i in range(webchan.IP_HOUR_MAX):
        c.post("/api/message", data={"text": f"iş {i}"}, headers={"x-forwarded-for": f"10.9.0.{i}"})
    n_before = len(calls)
    r = c.post("/api/message", data={"text": "kaçak?"}, headers={"x-forwarded-for": "10.9.9.9"})
    assert "rate limit" in r.json()["replies"][0]["text"].lower()
    assert len(calls) == n_before


def test_m5_global_counter_backed_by_modal_dict():
    import inspect
    assert "daily_counts" in inspect.signature(webchan.create_app).parameters
    src = (Path(__file__).resolve().parent.parent / "modal_app.py").read_text(encoding="utf-8")
    assert "Dict.from_name" in src and "demo" in src.lower() and "count" in src.lower()


def _sid_client(app_obj, client):
    sid = client.cookies.get("cirak_sid")
    assert sid
    return sid


def test_m6_media_only_png_jpg_own_session_with_headers(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    monkeypatch.setattr(webchan.core, "handle", lambda *a, **k: [core.Reply("ok")])
    app_obj = webchan.create_app()
    a = TestClient(app_obj, base_url="https://testserver")
    b = TestClient(app_obj, base_url="https://testserver")
    a.get("/")
    b.get("/")
    sa, sb = _sid_client(app_obj, a), _sid_client(app_obj, b)
    mine = tmp_path / "web" / sa / "v.png"
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_bytes(PNG_BYTES)
    (tmp_path / "web" / sa / "evil.txt").write_bytes(b"hello")
    (tmp_path / "web" / sb / "v.png").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "web" / sb / "v.png").write_bytes(PNG_BYTES)
    good = a.get(f"/api/media/web/{sa}/v.png")
    assert good.status_code == 200 and good.headers["content-type"].startswith("image/")
    assert good.headers["x-content-type-options"] == "nosniff"
    assert good.headers["content-security-policy"] == "default-src 'none'"
    assert a.get(f"/api/media/web/{sa}/evil.txt").status_code == 404  # only png/jpg served
    assert b.get(f"/api/media/web/{sa}/v.png").status_code == 404     # not your session
    assert a.get(f"/api/media/web/{sb}/v.png").status_code == 404


def test_m6_upload_gets_server_chosen_extension(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    seen = []
    monkeypatch.setattr(webchan.core, "handle",
                        lambda s, kind, text="", media_path="", notify=None:
                        seen.append(media_path) or [core.Reply("ok")])
    c = TestClient(webchan.create_app())
    c.post("/api/message", files=[("images[]", ("evil.html", PNG_BYTES, "image/png"))])
    assert seen and seen[0].endswith(".png") and not seen[0].endswith(".html")
    c.post("/api/message", files=[("images[]", ("evil.txt", JPG_BYTES, "image/jpeg"))])
    assert seen[-1].endswith(".jpg")


def test_m7_trusted_host_and_origin(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    monkeypatch.setattr(webchan.core, "handle", lambda *a, **k: [core.Reply("ok")])
    c = TestClient(webchan.create_app())
    assert c.get("/", headers={"host": "evil.com"}).status_code == 400
    assert c.post("/api/message", data={"text": "selam"},
                  headers={"origin": "https://evil.com"}).status_code == 403
    assert c.post("/api/message", data={"text": "selam"},
                  headers={"origin": "http://testserver"}).status_code == 200
    assert c.post("/api/message", data={"text": "selam"}).status_code == 200  # no origin: allow


def test_l6_session_creation_rate_limited(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    app_obj = webchan.create_app()
    statuses = [TestClient(app_obj).get("/",
                headers={"x-forwarded-for": "7.7.7.7"}).status_code
                for _ in range(webchan.SESSION_CREATE_HOUR_MAX + 5)]
    assert 429 in statuses


def test_l7_secure_cookie_only_in_public_mode(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    cfg("public_demo", False)
    assert "secure" not in TestClient(webchan.create_app()).get("/").headers.get("set-cookie", "").lower()
    cfg("public_demo", True)
    assert "secure" in TestClient(webchan.create_app()).get("/").headers.get("set-cookie", "").lower()


# --------------------------------------------------------------------------- #
# task 16: landing page + read-only trace
# --------------------------------------------------------------------------- #

def _seed_run(out_dir, sid_tail, entries):
    run = out_dir / "runs" / sid_tail / "20260101-000000-abcdef"
    run.mkdir(parents=True, exist_ok=True)
    import json
    (run / "tool_log.json").write_text(json.dumps(entries), encoding="utf-8")
    return run


_TRACE_LOG = [
    {"tool": "get_store_profile", "args": {}, "result": {"business_name": "Nar Butik"}},
    {"tool": "find_customers", "args": {"inactive_days": 60},
     "result": {"segment_id": "s1", "count": 23, "sample": ["Ayşe", "905321234567"]}},
    {"tool": "propose_actions", "errors": []},
]


def test_t16_trace_returns_only_tool_names_and_counts(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    run = _seed_run(tmp_path, "aaa1", _TRACE_LOG)
    (run / "crops").mkdir()
    (run / "crops" / "tmp1.png").write_bytes(b"x")
    (run / "campaign_visual.png").write_bytes(b"x")
    c = TestClient(webchan.create_app())
    c.cookies.set("cirak_sid", "aaaaaaa1")
    r = c.get("/api/trace")
    assert r.status_code == 200
    body = r.json()
    assert body["steps"] == [{"tool": "get_store_profile"},
                             {"tool": "find_customers", "count": 23},
                             {"tool": "propose_actions"}]
    assert body["models"] == list(dict.fromkeys([settings.orchestrator_model, settings.sam_model,
                                                 settings.vision_model, settings.image_model]))
    raw = r.text
    assert "args" not in raw and "result" not in raw
    assert "905321234567" not in raw and "Ayşe" not in raw and "Nar Butik" not in raw


def test_t16_trace_404_before_any_run(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    c = TestClient(webchan.create_app())
    assert c.get("/api/trace").status_code == 404


def test_t16_trace_isolated_per_session(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    _seed_run(tmp_path, "aaa1", _TRACE_LOG)
    app_obj = webchan.create_app()
    a = TestClient(app_obj)
    a.cookies.set("cirak_sid", "aaaaaaa1")
    assert a.get("/api/trace").status_code == 200
    b = TestClient(app_obj)
    b.cookies.set("cirak_sid", "bbbbbbb2")
    assert b.get("/api/trace").status_code == 404


def test_t16_coverage_json_shape():
    import json
    items = json.loads((webchan.STATIC_DIR / "coverage.json").read_text(encoding="utf-8"))
    assert isinstance(items, list) and len(items) >= 10
    for it in items:
        assert {"area", "what_en", "what_tr", "status"} <= set(it)
        assert it["status"] in ("live", "soon")
    assert any(it["status"] == "live" for it in items)
    assert any(it["status"] == "soon" for it in items)


def test_t16_index_has_toggle_and_coverage(client):
    assert 'id="langToggle"' in client.get("/").text
    assert 'id="coverage"' in client.get("/").text
