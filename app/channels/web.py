"""WhatsApp-style web UI for the demo. Everything goes through `app.core.handle`."""

from __future__ import annotations

import html
import logging
import re
import secrets
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .. import core
from ..brief import build_morning_brief
from ..config import settings

log = logging.getLogger(__name__)

COOKIE = "cirak_sid"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_FAIL_MSG = "Bir sorun çıktı, hiçbir şey yapılmadı. Tekrar dener misin?"
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _valid_sid(sid: str) -> bool:
    return bool(_SID_RE.match(sid))


def _safe_filename(name: str, default: str) -> str:
    base = Path(name).name.strip() or default
    base = _SAFE_RE.sub("_", base).strip("._") or default
    return base[-80:]


def _image_url(image_path: str) -> str | None:
    if not image_path:
        return None
    try:
        base = settings.out_dir.resolve()
        p = Path(image_path)
        full = p if p.is_absolute() else (base / p)
        rel = full.resolve().relative_to(base)
    except (ValueError, OSError):
        try:
            rel = Path(image_path).relative_to(settings.out_dir)
        except ValueError:
            return None
    if ".." in rel.parts:
        return None
    return f"/api/media/{rel.as_posix()}"


def _business_name(session: core.Session) -> str:
    try:
        return str(session.get_backend().store_profile().get("business_name", session.business))
    except Exception:  # noqa: BLE001 — header must render even if the backend is unreachable
        return session.business


def _reply_json(r: core.Reply) -> dict[str, Any]:
    return {"text": r.text, "image_url": _image_url(r.image_path)}


def create_app() -> FastAPI:
    sessions: dict[str, core.Session] = {}
    locks: dict[str, threading.Lock] = {}
    guard = threading.Lock()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    def _ensure(request: Request) -> tuple[str, core.Session, bool]:
        sid = request.cookies.get(COOKIE, "")
        if sid and _valid_sid(sid):
            with guard:
                if sid in sessions:
                    return sid, sessions[sid], False
                sess = core.Session(sender=f"web-{sid}")
                sessions[sid] = sess
                locks.setdefault(sid, threading.Lock())
                return sid, sess, False
        sid = secrets.token_urlsafe(16)
        sess = core.Session(sender=f"web-{sid}")
        with guard:
            sessions[sid] = sess
            locks.setdefault(sid, threading.Lock())
        return sid, sess, True

    def _lock(sid: str) -> threading.Lock:
        with guard:
            return locks.setdefault(sid, threading.Lock())

    app = FastAPI(title="Çırak web", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        sid, session, is_new = _ensure(request)
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        page = page.replace("{{BUSINESS}}", html.escape(_business_name(session)))
        badge = "KEDD" if session.business == "kedd" else "demo"
        page = page.replace("{{BADGE}}", badge).replace(
            "{{BADGE_CLASS}}", "kedd" if badge == "KEDD" else "demo")
        resp = HTMLResponse(page)
        if is_new:
            resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax")
        return resp

    def _safe_handle(session: core.Session, kind: str, text: str, media: str,
                       status: list[str]) -> list[core.Reply]:
        try:
            return core.handle(session, kind, text=text, media_path=media, notify=status.append)
        except Exception:  # noqa: BLE001 — never leave the demo chat hanging
            log.exception("web handle failed")
            return [core.Reply(_FAIL_MSG)]

    @app.post("/api/message")
    async def post_message(request: Request) -> JSONResponse:
        sid, session, is_new = _ensure(request)
        form = await request.form()
        raw_text = form.get("text", "")
        text = (raw_text if isinstance(raw_text, str) else "").strip()
        audio = form.get("audio")
        uploads: list[tuple[str, bytes]] = []
        for key in ("images[]", "images", "image"):
            for f in form.getlist(key):
                if isinstance(f, UploadFile) and f.filename:
                    content = await f.read()
                    if content:
                        uploads.append((f.filename, content))
        audio_name, audio_content = "", b""
        if isinstance(audio, UploadFile) and audio.filename:
            audio_content = await audio.read()
            audio_name = audio.filename if audio_content else ""
        if not text and not audio_name and not uploads:
            resp = JSONResponse(status_code=400, content={"detail": "Boş mesaj."})
            if is_new:
                resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax")
            return resp

        def _work() -> dict[str, Any]:
            with _lock(sid):
                up_dir = settings.out_dir / "web" / sid
                up_dir.mkdir(parents=True, exist_ok=True)

                def _store(name: str, content: bytes, default: str) -> str:
                    dest = up_dir / f"{uuid.uuid4().hex[:8]}_{_safe_filename(name, default)}"
                    dest.write_bytes(content)
                    return str(dest)

                status: list[str] = []
                replies: list[core.Reply] = []
                for name, content in uploads:
                    replies.extend(_safe_handle(session, "image", "", _store(name, content, "image.jpg"),
                                                status))
                if text:
                    replies.extend(_safe_handle(session, "text", text, "", status))
                elif audio_name:
                    replies.extend(_safe_handle(session, "audio", "",
                                                _store(audio_name, audio_content, "voice.webm"), status))
                return {"replies": [_reply_json(r) for r in replies], "status": status,
                        "business": session.business, "business_name": _business_name(session)}

        data = await run_in_threadpool(_work)
        resp = JSONResponse(data)
        if is_new:
            resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax")
        return resp

    @app.post("/api/brief")
    async def post_brief(request: Request) -> JSONResponse:
        sid, session, is_new = _ensure(request)

        def _work() -> dict[str, Any]:
            with _lock(sid):
                try:
                    replies = build_morning_brief(session)
                except Exception:  # noqa: BLE001 — never leave the demo chat hanging
                    log.exception("brief failed")
                    replies = [core.Reply(_FAIL_MSG)]
                return {"replies": [_reply_json(r) for r in replies], "status": [],
                        "business": session.business, "business_name": _business_name(session)}

        data = await run_in_threadpool(_work)
        resp = JSONResponse(data)
        if is_new:
            resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax")
        return resp

    @app.post("/api/reset")
    async def post_reset(request: Request) -> JSONResponse:
        sid, session, is_new = _ensure(request)

        def _work() -> dict[str, Any]:
            with _lock(sid):
                status: list[str] = []
                replies = _safe_handle(session, "text", "/sifirla", "", status)
                return {"replies": [_reply_json(r) for r in replies], "status": status,
                        "business": session.business, "business_name": _business_name(session)}

        data = await run_in_threadpool(_work)
        resp = JSONResponse(data)
        if is_new:
            resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax")
        return resp

    @app.get("/api/media/{rel_path:path}", response_model=None)
    async def get_media(rel_path: str):
        bad = not rel_path or rel_path.startswith(("/", "\\")) or ".." in Path(rel_path).parts
        if bad:
            return JSONResponse(status_code=400, content={"detail": "Geçersiz yol."})
        base = settings.out_dir.resolve()
        full = (base / rel_path).resolve()
        try:
            full.relative_to(base)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Geçersiz yol."})
        if not full.is_file():
            return JSONResponse(status_code=404, content={"detail": "Bulunamadı."})
        return FileResponse(str(full))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
