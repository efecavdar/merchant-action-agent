"""Web UI channel: sessions, uploads through core.handle, media guard, per-session lock."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import core
from app.channels import web as webchan
from app.config import settings


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
    visual = tmp_path / "runs" / "x" / "campaign_visual.png"
    visual.parent.mkdir(parents=True, exist_ok=True)
    visual.write_bytes(b"\x89PNG-fake")

    def fake_handle(session, kind, text="", media_path="", notify=None):
        return [core.Reply("Hallettim.", image_path=str(visual))]

    monkeypatch.setattr(webchan.core, "handle", fake_handle)
    r = client.post("/api/message", data={"text": "evet"})
    url = r.json()["replies"][0]["image_url"]
    assert url and url.startswith("/api/media/")
    got = client.get(url)
    assert got.status_code == 200 and got.content == b"\x89PNG-fake"


def test_media_rejects_traversal(client, tmp_path):
    (tmp_path / "ok.png").write_bytes(b"ok")
    assert client.get("/api/media/ok.png").status_code == 200
    assert client.get("/api/media/../secret").status_code in (400, 404)
    assert client.get("/api/media/%2e%2e/secret").status_code in (400, 404)
    assert client.get("/api/media/missing.png").status_code == 404


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
