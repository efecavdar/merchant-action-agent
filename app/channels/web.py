"""WhatsApp-style web UI for the demo. Everything goes through `app.core.handle`.

Public mode (`PUBLIC_DEMO=true`, the Modal `demo` function for judges) adds: the live
KEDD demo business by default (mock fallback + panel when KEDD is down, `/kedd` refused),
per-IP and global rate limits, an approved-plans cap, upload checks, session
expiry/eviction, and a banner. Local behaviour is unchanged when the flag is off.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import secrets
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import core
from ..brief import build_morning_brief
from ..config import settings
from ..tools.backend import KeddBackend, KeddError, MockBackend

Image.MAX_IMAGE_PIXELS = 40_000_000

log = logging.getLogger(__name__)

COOKIE = "cirak_sid"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_FAIL_MSG = "Bir sorun çıktı, hiçbir şey yapılmadı. Tekrar dener misin?"
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Public-demo safety rails (rules 1-4 of docs/tasks/13-public-demo.md).
SESSION_IDLE_S = 30 * 60
MAX_SESSIONS = 500
IP_HOUR_MAX = 20
IP_DAY_MAX = 60
MAX_TRACKED_IPS = 2000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 2 * 1024 * 1024
# Review hardening (M4/M5/L6).
MAX_CONTENT_BYTES = 22 * 1024 * 1024
MAX_TEXT_CHARS = 1000
MAX_FORM_FILES = 5
MAX_FORM_FIELDS = 5
SESS_HOUR_MAX = 20
SESS_DAY_MAX = 60
SESSION_CREATE_HOUR_MAX = 20
SESSION_CREATE_DAY_MAX = 60
# Live KEDD demo (task 17).
KEDD_DEMO_BUSINESS = "kedd-demo"
CAP_APPROVED_HOUR_MAX = 3
KEDD_STATE_CACHE_S = 10
KEDD_STATE_TIMEOUT_S = 10

_KEDD_REFUSAL = "Bu demo yalnız örnek işletmelerle çalışır."
_IP_LIMIT_MSG = ("Hız sınırına takıldın, biraz sonra tekrar dener misin? ⏳ "
                 "You've hit the demo rate limit, please try again in a bit.")
_GLOBAL_LIMIT_MSG = ("Bugünkü demo kotası doldu, yarın tekrar bekleriz. ⏳ "
                     "Today's demo quota is used up, please come back tomorrow.")
_TOO_MANY_IMAGES = "Bir mesaja en fazla 4 fotoğraf ekleyebilirsin. 📷 Up to 4 photos per message."
_IMAGE_TOO_BIG = "Fotoğraflar 5 MB'tan küçük olmalı. 📷 Photos must be under 5 MB each."
_IMAGE_BAD_TYPE = "Yalnızca jpg veya png fotoğraf gönderebilirsin. 📷 Photos must be jpg or png."
_AUDIO_TOO_BIG = "Sesli not 2 MB'tan küçük olmalı. 🎙️ Voice notes must be under 2 MB."
_TEXT_TOO_LONG = ("Mesajın biraz uzun, 1000 karakterden kısa dener misin? ✂️ "
                  "Please keep messages under 1000 characters.")
_TOO_LARGE = ("Mesajın çok büyük, daha küçük dener misin? 📦 Your message is too large.")
_KEDD_DOWN = ("KEDD'ye şu an ulaşamıyorum, yazdıkların duruyor; birazdan tekrar dener misin? 🔌 "
              "I can't reach KEDD right now, please try again in a bit.")
_CAP_MSG = ("Saatte 3 planlık demo kotan doldu, örnek salonda devam ediyorsun. 🏁 "
            "You've used your 3 demo plans for this hour, continuing on the sample salon.")
_FREE_COMMANDS = {"/butik", "/salon", "/sifirla", "/reset", "/kedd"}
_FREE_GREETINGS = {"/yardim", "/help", "yardım", "merhaba", "selam"}
_BANNER_HTML = ('<div class="demobanner">Demo: örnek işletme verisi · Synthetic demo data · '
                'Nothing is sent to real customers</div>')
_MEDIA_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"}


def _now() -> float:
    return time.time()


def _utc_day(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).date().isoformat()


def _valid_sid(sid: str) -> bool:
    return bool(_SID_RE.match(sid))


def _norm_command(text: str) -> str:
    # Mirrors core._norm's command routing (core.py is out of scope for this task).
    return text.strip().lower().strip(".!?,👍 ")


def _client_ip(request: Request) -> str:
    # Behind Modal's proxy the peer address is internal, so use X-Forwarded-For — but the
    # leftmost entry is client-controlled ("spoofed, real" after the proxy appends), so key
    # on the proxy-appended rightmost hop. Spoofing it only evades the spoofer's own quota;
    # the per-session cap and the global cap still hold.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
        return "unknown"
    return request.client.host if request.client else "unknown"


def _is_free_request(text: str, has_media: bool, session: core.Session) -> bool:
    """Commands and (pending-free) greetings make no model calls, so they burn no quota."""
    if has_media:
        return False
    t = _norm_command(text)
    if t in _FREE_COMMANDS:
        return True
    return t in _FREE_GREETINGS and not session.pending


def _prune(hits: list[float], now: float) -> list[float]:
    return [t for t in hits if now - t < 24 * 3600]


def _over(hits: list[float], now: float, hour_max: int, day_max: int) -> bool:
    hour = sum(1 for t in hits if now - t < 3600)
    return hour >= hour_max or len(hits) >= day_max


def _limit_hit(ip_hits: dict[str, list[float]], sess_hits: dict[str, list[float]],
               glob: Any, ip: str, sid: str, now: float, daily_cap: int) -> str | None:
    """None when the request may proceed (recording the hit); "ip"/"global" when refused.

    The caller must hold the guard: check-then-record is one atomic step, and a refusal
    burns neither quota. `glob` is a dict-like with "day"/"count" keys — a plain dict
    locally, a modal.Dict in the deployed demo.
    """
    day = _utc_day(now)
    if glob.get("day", "") != day:
        glob["day"], glob["count"] = day, 0
    ip_list = _prune(ip_hits.get(ip, []), now)
    sess_list = _prune(sess_hits.get(sid, []), now)
    if _over(ip_list, now, IP_HOUR_MAX, IP_DAY_MAX) or _over(sess_list, now, SESS_HOUR_MAX, SESS_DAY_MAX):
        ip_hits[ip], sess_hits[sid] = ip_list, sess_list
        return "ip"
    if int(glob.get("count", 0)) >= daily_cap:
        ip_hits[ip], sess_hits[sid] = ip_list, sess_list
        return "global"
    ip_list.append(now)
    sess_list.append(now)
    ip_hits[ip], sess_hits[sid] = ip_list, sess_list
    glob["count"] = int(glob.get("count", 0)) + 1
    if len(ip_hits) > MAX_TRACKED_IPS:
        for stale in [k for k, v in ip_hits.items() if not v or now - v[-1] >= 24 * 3600]:
            del ip_hits[stale]
    return None


def _create_limited(create_hits: dict[str, list[float]], ip: str, now: float) -> bool:
    """True when this IP already created too many sessions; records the hit otherwise."""
    hits = _prune(create_hits.get(ip, []), now)
    if _over(hits, now, SESSION_CREATE_HOUR_MAX, SESSION_CREATE_DAY_MAX):
        create_hits[ip] = hits
        return True
    hits.append(now)
    create_hits[ip] = hits
    return False


def _demo_creds_ready() -> bool:
    """The demo credential triple is configured, so kedd-demo sessions can be built."""
    return bool(settings.kedd_api_url and settings.kedd_demo_secret and settings.kedd_demo_business)


def _cap_hit(approve_hits: dict[str, list[float]], ip: str, now: float) -> bool:
    """True when this IP already approved enough kedd-demo plans this hour; else records."""
    hits = [t for t in approve_hits.get(ip, []) if now - t < 3600]
    if len(hits) >= CAP_APPROVED_HOUR_MAX:
        approve_hits[ip] = hits
        return True
    hits.append(now)
    approve_hits[ip] = hits
    if len(approve_hits) > MAX_TRACKED_IPS:
        for stale in [k for k, v in approve_hits.items() if not v or now - v[-1] >= 3600]:
            del approve_hits[stale]
    return False


def _drop_expired(sessions: dict[str, core.Session], last_seen: dict[str, float],
                  locks: dict[str, threading.Lock], now: float) -> None:
    for sid in [s for s, at in last_seen.items() if now - at > SESSION_IDLE_S]:
        sessions.pop(sid, None)
        locks.pop(sid, None)
        last_seen.pop(sid, None)


def _is_jpg_png(content: bytes) -> bool:
    return content[:3] == b"\xff\xd8\xff" or content[:8] == b"\x89PNG\r\n\x1a\n"


def _server_ext(content: bytes, default_ext: str) -> str:
    if content[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return default_ext


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
    if ".." in rel.parts or not rel.parts or rel.parts[0] != "web":
        return None
    low = rel.as_posix().lower()
    if not (low.endswith(".png") or low.endswith(".jpg") or low.endswith(".jpeg")):
        return None
    return f"/api/media/{rel.as_posix()}"


def _business_name(session: core.Session) -> str:
    try:
        return str(session.get_backend().store_profile().get("business_name", session.business))
    except Exception:  # noqa: BLE001 — header must render even if the backend is unreachable
        return session.business


def _reply_json(r: core.Reply) -> dict[str, Any]:
    return {"text": r.text, "image_url": _image_url(r.image_path)}


def _latest_trace_dir(sid: str) -> Path | None:
    """Newest run dir of this session holding a readable tool_log.json, if any."""
    runs = settings.out_dir / "runs" / sid[-4:]
    if not runs.is_dir():
        return None
    try:
        cands = sorted((d for d in runs.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)
    except OSError:
        return None
    for d in cands:
        try:
            entries = json.loads((d / "tool_log.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(entries, list):
            return d
    return None


def _trace_json(run_dir: Path) -> dict[str, Any]:
    """Tool names (+ segment counts) and model ids for the landing-page timeline.

    Never args, results, names or message text: only what proves the run happened.
    """
    entries = json.loads((run_dir / "tool_log.json").read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("tool"), str):
            continue
        step: dict[str, Any] = {"tool": e["tool"][:64]}
        if e["tool"] == "find_customers":
            res = e.get("result")
            count = res.get("count") if isinstance(res, dict) else None
            if isinstance(count, int) and not isinstance(count, bool):
                step["count"] = count
        steps.append(step)
    models = [settings.orchestrator_model]  # tool_log.json exists only after a plan ran
    try:
        has_crops = any((run_dir / "crops").iterdir())
    except OSError:
        has_crops = False
    if has_crops:  # crops on disk ⟺ SAM cut-outs + Spark vision both ran
        models += [settings.sam_model, settings.vision_model]
    if (run_dir / "campaign_visual.png").is_file():
        models.append(settings.image_model)
    return {"steps": steps, "models": list(dict.fromkeys(models))}


def _recent_writes(rep: dict[str, Any]) -> list[dict[str, str]]:
    """Session writes as PII-free kind/label rows for the Live-in-KEDD panel."""
    out: list[dict[str, str]] = []
    for i in rep.get("items_added") or []:
        out.append({"kind": "catalog", "label": f"{i.get('name', '')} · {i.get('price')}"})
    for d in rep.get("active_discounts") or []:
        out.append({"kind": "discount", "label": f"{d.get('name', '')} (%{d.get('percent')})"})
    for c in rep.get("campaigns") or []:
        out.append({"kind": "campaign", "label": f"{c.get('name', '')} → {c.get('recipients')} kişi"})
    for p in rep.get("social_posts") or []:
        plats = ",".join(p.get("platforms") or [])
        out.append({"kind": "social", "label": f"{str(p.get('caption', ''))[:60]} ({plats})"})
    for a in rep.get("appointments_changed") or []:
        out.append({"kind": "appointment", "label": f"{a.get('change', '')}: {a.get('starts', '')}"})
    return out[-5:]


def _bare_state(state: str, business: str, error: str | None) -> dict[str, Any]:
    return {"state": state, "business": business, "business_name": business,
            "catalog_sample": [], "appointments_today": {"count": 0, "items": []},
            "recent_writes": [], "error": error}


def _snapshot(backend: Any, business: str) -> dict[str, Any]:
    """Panel rows from any backend. Customer names/phones are dropped at the mapping edge."""
    profile = backend.store_profile()
    items = backend.list_catalog()[:5]
    appts = backend.get_schedule(date.today().isoformat()).get("appointments") or []
    if isinstance(backend, KeddBackend):
        writes: list[dict[str, str]] = []  # gateway /agent/report has no row shape to map yet
    else:
        writes = _recent_writes(backend.report())
    return {
        "business": business,
        "business_name": str(profile.get("business_name", business)),
        "catalog_sample": [{"name": str(i.get("name", "")), "price": i.get("price")} for i in items],
        "appointments_today": {
            "count": len(appts),
            "items": [{"time": str(a.get("time", "")), "service": str(a.get("service", "")),
                       "staff": str(a.get("staff", ""))} for a in appts[:5]]},
        "recent_writes": writes,
    }


def _fallback_state(error: str) -> dict[str, Any]:
    """The panel's other surface: mock salon data plus the triggering error, never a hang."""
    try:
        snap = _snapshot(MockBackend("salon"), "salon")
    except Exception:  # noqa: BLE001 — even the fallback must render
        return _bare_state("fallback", "salon", error[:200])
    return {"state": "fallback", **snap, "error": error[:200]}


def _kedd_state(session: core.Session, fresh: bool, cache: dict[str, Any]) -> dict[str, Any]:
    """Live panel payload: cached KEDD reads, mock data for mock sessions, salon fallback."""
    business = session.business
    if business not in (KEDD_DEMO_BUSINESS, "kedd"):
        try:
            return {"state": "mock", **_snapshot(session.get_backend(), business), "error": None}
        except Exception:  # noqa: BLE001 — a broken mock must not 500 the panel
            log.warning("kedd-state: mock snapshot failed")
            return _bare_state("mock", business, None)
    now, key = _now(), business
    if not fresh and cache.get("data") is not None and cache.get("key") == key \
            and now - cache.get("at", 0.0) < KEDD_STATE_CACHE_S:
        return cache["data"]
    try:
        backend = session.get_backend()
        if not isinstance(backend, KeddBackend):
            session.backend = None  # a mock here means the guard never ran; rebuild live
            backend = session.get_backend()
        data = {"state": "live", **_snapshot(backend, business), "error": None}
    except KeddError as e:
        data = _fallback_state(str(e))
    except Exception:  # noqa: BLE001 — panel must render even on unexpected backend errors
        log.warning("kedd-state: live probe failed")
        data = _fallback_state("KEDD'ye ulaşılamadı.")
    cache["at"], cache["key"], cache["data"] = now, key, data  # benign if two probes race
    return data


def _log_result(endpoint: str, data: dict[str, Any], started: float) -> None:
    # Public-demo rule: logs carry counts and timings only, never user content.
    log.info("%s: replies=%d notifications=%d ms=%d", endpoint, len(data["replies"]),
             len(data["status"]), int((time.monotonic() - started) * 1000))


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    if not origin:
        return True
    try:
        host = (request.headers.get("host", "") or "").split(":")[0].lower()
        ohost = (urlparse(origin).hostname or "").lower()
    except ValueError:
        return False
    return bool(ohost) and ohost == host


def create_app(daily_counts: Any | None = None) -> FastAPI:
    sessions: dict[str, core.Session] = {}
    locks: dict[str, threading.Lock] = {}
    last_seen: dict[str, float] = {}
    ip_hits: dict[str, list[float]] = {}
    sess_hits: dict[str, list[float]] = {}
    create_hits: dict[str, list[float]] = {}
    approve_hits: dict[str, list[float]] = {}
    kedd_cache: dict[str, Any] = {"at": 0.0, "key": "", "data": None}
    glob: Any = daily_counts if daily_counts is not None else {"day": "", "count": 0}
    guard = threading.Lock()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    def _ensure(request: Request) -> tuple[str, core.Session | None, bool]:
        sid = request.cookies.get(COOKIE, "")
        now = _now()
        with guard:
            if settings.public_demo:
                _drop_expired(sessions, last_seen, locks, now)
            if sid and _valid_sid(sid) and sid in sessions:
                last_seen[sid] = now
                return sid, sessions[sid], False
            if sid and _valid_sid(sid):
                pass  # valid cookie, unknown session: adopt the sid below without re-setting it
            else:
                sid = secrets.token_urlsafe(16)
            if settings.public_demo and _create_limited(create_hits, _client_ip(request), now):
                return "", None, False
            if settings.public_demo:
                while len(sessions) >= MAX_SESSIONS and last_seen:
                    oldest = min(last_seen, key=lambda s: last_seen[s])
                    sessions.pop(oldest, None)
                    locks.pop(oldest, None)
                    last_seen.pop(oldest, None)
            is_new = sid not in sessions
            sess = core.Session(sender=f"web-{sid}")
            if settings.public_demo and _demo_creds_ready():
                sess.business = KEDD_DEMO_BUSINESS  # backend stays lazy; first read builds Kedd(demo)
            sessions[sid] = sess
            locks.setdefault(sid, threading.Lock())
            last_seen[sid] = now
            return sid, sess, is_new and not (request.cookies.get(COOKIE) == sid)

    def _lock(sid: str) -> threading.Lock:
        with guard:
            return locks.setdefault(sid, threading.Lock())

    def _public_guard(session: core.Session) -> None:
        # Pin public sessions to safe backends: mock businesses get a MockBackend, and the
        # kedd-demo business gets ONLY a demo-key KeddBackend — a main-key backend here is
        # dropped so the next read rebuilds the demo one lazily. Never raises.
        if not settings.public_demo:
            return
        if session.business == "kedd":
            session.business, session.backend = settings.mock_business, None
        if session.business == KEDD_DEMO_BUSINESS:
            backend = session.backend
            ok = (isinstance(backend, KeddBackend) and getattr(backend, "_key_id", "main") == "demo"
                  and getattr(backend, "_schema", None) == KeddBackend.SCHEMA)
            if backend is not None and not ok:
                session.backend = None
            return
        backend = session.backend
        stale = getattr(backend, "_schema", None) != MockBackend.SCHEMA
        if backend is None or not isinstance(backend, MockBackend) or stale:
            try:
                session.backend = MockBackend(business=session.business)
            except ValueError:
                session.backend = None

    app = FastAPI(title="Çırak web", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver", "*.modal.run"])
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def _origin_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method == "POST" and not _origin_ok(request):
            return JSONResponse(status_code=403, content={"detail": "Geçersiz istek."})
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):  # type: ignore[no-untyped-def]
        sid, session, is_new = _ensure(request)
        if session is None:
            return HTMLResponse(_IP_LIMIT_MSG, status_code=429)
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        page = page.replace("{{BUSINESS}}", html.escape(_business_name(session)))
        if session.business == KEDD_DEMO_BUSINESS:
            badge, bclass = "KEDD demo", "kedd"
        elif session.business == "kedd":
            badge, bclass = "KEDD", "kedd"
        else:
            badge, bclass = "demo", "demo"
        page = page.replace("{{BADGE}}", badge).replace("{{BADGE_CLASS}}", bclass)
        page = page.replace("{{BANNER}}", _BANNER_HTML if settings.public_demo else "")
        resp = HTMLResponse(page)
        if is_new:
            resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax", secure=settings.public_demo)
        return resp

    def _safe_handle(session: core.Session, kind: str, text: str, media: str,
                       status: list[str]) -> list[core.Reply]:
        try:
            _public_guard(session)
            return core.handle(session, kind, text=text, media_path=media, notify=status.append)
        except KeddError:
            # The session keeps its kedd-demo state; the next message retries KEDD.
            log.warning("web handle kedd failed")
            return [core.Reply(_KEDD_DOWN)]
        except Exception as e:  # noqa: BLE001 — never leave the demo chat hanging
            if settings.public_demo:
                log.warning("web handle failed: %s", type(e).__name__)
            else:
                log.exception("web handle failed")
            return [core.Reply(_FAIL_MSG)]

    def _finalize(data: dict[str, Any], is_new: bool, sid: str, status_code: int = 200) -> JSONResponse:
        resp = JSONResponse(data, status_code=status_code)
        if is_new:
            resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax", secure=settings.public_demo)
        return resp

    def _refusal(session: core.Session, message: str) -> dict[str, Any]:
        # Refusals ride the normal shape so the UI renders the reason as a chat bubble.
        _public_guard(session)
        return {"replies": [{"text": message, "image_url": None}], "status": [],
                "business": session.business, "business_name": _business_name(session)}

    @app.post("/api/message")
    async def post_message(request: Request) -> JSONResponse:
        started = time.monotonic()
        try:
            claimed = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            claimed = 0
        if claimed > MAX_CONTENT_BYTES:
            return JSONResponse(status_code=413, content={"detail": _TOO_LARGE})
        sid, session, is_new = _ensure(request)
        if session is None:
            return JSONResponse(status_code=429, content={"detail": _IP_LIMIT_MSG})
        try:
            form = await request.form(max_files=MAX_FORM_FILES, max_fields=MAX_FORM_FIELDS,
                                      max_part_size=MAX_CONTENT_BYTES)
        except (MultiPartException, HTTPException, ValueError):
            log.info("message: form limits hit")
            return _finalize({"detail": _TOO_LARGE}, is_new, sid, 413)
        raw_text = form.get("text", "")
        text = (raw_text if isinstance(raw_text, str) else "").strip()
        public = settings.public_demo

        # Count files before reading a single byte.
        image_files: list[UploadFile] = []
        for key in ("images[]", "images", "image"):
            for f in form.getlist(key):
                if isinstance(f, UploadFile) and f.filename:
                    image_files.append(f)
        audio_file = form.get("audio")
        audio_upload = audio_file if isinstance(audio_file, UploadFile) and audio_file.filename else None
        if public and len(text) > MAX_TEXT_CHARS:
            return _finalize(_refusal(session, _TEXT_TOO_LONG), is_new, sid)
        if public and len(image_files) > MAX_IMAGES:
            return _finalize(_refusal(session, _TOO_MANY_IMAGES), is_new, sid)

        async def _read_capped(f: UploadFile, limit: int) -> bytes:
            return await f.read(limit + 1) if public else await f.read()

        uploads: list[tuple[str, bytes]] = []
        for f in image_files:
            content = await _read_capped(f, MAX_IMAGE_BYTES)
            if content:
                uploads.append((f.filename or "image", content))
        audio_name, audio_content = "", b""
        if audio_upload is not None:
            audio_content = await _read_capped(audio_upload, MAX_AUDIO_BYTES)
            audio_name = audio_upload.filename if audio_content else ""
        if not text and not audio_name and not uploads:
            return _finalize({"detail": "Boş mesaj."}, is_new, sid, 400)

        if public:
            problem = _check_uploads(uploads, audio_content)
            if problem:
                return _finalize(_refusal(session, problem), is_new, sid)
            if _norm_command(text) == "/kedd":
                return _finalize(_refusal(session, _KEDD_REFUSAL), is_new, sid)
            if not _is_free_request(text, bool(uploads) or bool(audio_name), session):
                with guard:
                    limited = _limit_hit(ip_hits, sess_hits, glob, _client_ip(request), sid,
                                         _now(), settings.public_demo_daily_cap)
                if limited == "ip":
                    log.info("message: limited=ip")
                    return _finalize(_refusal(session, _IP_LIMIT_MSG), is_new, sid)
                if limited == "global":
                    log.info("message: limited=global")
                    return _finalize(_refusal(session, _GLOBAL_LIMIT_MSG), is_new, sid)
            if (session.business == KEDD_DEMO_BUSINESS and session.pending
                    and not (uploads or audio_name) and _norm_command(text) in core.YES):
                with guard:
                    capped = _cap_hit(approve_hits, _client_ip(request), _now())
                if capped:
                    # One-way flip: the pending KEDD plan is discarded, never run.
                    with _lock(sid):
                        session.business, session.backend, session.pending = "salon", None, None
                    log.info("message: capped")
                    return _finalize(_refusal(session, _CAP_MSG), is_new, sid)

        def _work() -> dict[str, Any]:
            with _lock(sid):
                up_dir = settings.out_dir / "web" / sid
                up_dir.mkdir(parents=True, exist_ok=True)

                def _store(name: str, content: bytes, default: str) -> str:
                    ext = _server_ext(content, Path(default).suffix or ".jpg")
                    stem = Path(name).stem.strip() or Path(default).stem
                    dest = up_dir / f"{uuid.uuid4().hex[:8]}_{_safe_filename(stem, 'file')}{ext}"
                    dest.write_bytes(content)
                    return str(dest)

                def _adopt_visual(reply: core.Reply) -> None:
                    if not reply.image_path:
                        return
                    try:
                        Path(reply.image_path).resolve().relative_to(up_dir.resolve())
                        return  # already ours
                    except (ValueError, OSError):
                        pass
                    try:
                        content = Path(reply.image_path).read_bytes()
                    except OSError:
                        reply.image_path = ""
                        return
                    if not _is_jpg_png(content):
                        reply.image_path = ""
                        return
                    dest = up_dir / f"visual_{uuid.uuid4().hex[:8]}{_server_ext(content, '.png')}"
                    try:
                        dest.write_bytes(content)
                    except OSError:
                        reply.image_path = ""
                        return
                    reply.image_path = str(dest)

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
                for r in replies:
                    _adopt_visual(r)
                return {"replies": [_reply_json(r) for r in replies], "status": status,
                        "business": session.business, "business_name": _business_name(session)}

        data = await run_in_threadpool(_work)
        _log_result("message", data, started)
        return _finalize(data, is_new, sid)

    @app.post("/api/brief")
    async def post_brief(request: Request) -> JSONResponse:
        started = time.monotonic()
        sid, session, is_new = _ensure(request)
        if session is None:
            return JSONResponse(status_code=429, content={"detail": _IP_LIMIT_MSG})
        if settings.public_demo:
            with guard:
                limited = _limit_hit(ip_hits, sess_hits, glob, _client_ip(request), sid,
                                     _now(), settings.public_demo_daily_cap)
            if limited == "ip":
                log.info("brief: limited=ip")
                return _finalize(_refusal(session, _IP_LIMIT_MSG), is_new, sid)
            if limited == "global":
                log.info("brief: limited=global")
                return _finalize(_refusal(session, _GLOBAL_LIMIT_MSG), is_new, sid)

        def _work() -> dict[str, Any]:
            with _lock(sid):
                _public_guard(session)
                try:
                    replies = build_morning_brief(session)
                except KeddError:
                    log.warning("brief kedd failed")
                    replies = [core.Reply(_KEDD_DOWN)]
                except Exception as e:  # noqa: BLE001 — never leave the demo chat hanging
                    if settings.public_demo:
                        log.warning("brief failed: %s", type(e).__name__)
                    else:
                        log.exception("brief failed")
                    replies = [core.Reply(_FAIL_MSG)]
                for r in replies:
                    if r.image_path:
                        try:
                            up_dir = settings.out_dir / "web" / sid
                            Path(r.image_path).resolve().relative_to(up_dir.resolve())
                        except (ValueError, OSError):
                            r.image_path = ""
                return {"replies": [_reply_json(r) for r in replies], "status": [],
                        "business": session.business, "business_name": _business_name(session)}

        data = await run_in_threadpool(_work)
        _log_result("brief", data, started)
        return _finalize(data, is_new, sid)

    @app.post("/api/reset")
    async def post_reset(request: Request) -> JSONResponse:
        started = time.monotonic()
        sid, session, is_new = _ensure(request)
        if session is None:
            return JSONResponse(status_code=429, content={"detail": _IP_LIMIT_MSG})

        def _work() -> dict[str, Any]:
            with _lock(sid):
                status: list[str] = []
                replies = _safe_handle(session, "text", "/sifirla", "", status)
                return {"replies": [_reply_json(r) for r in replies], "status": status,
                        "business": session.business, "business_name": _business_name(session)}

        data = await run_in_threadpool(_work)
        _log_result("reset", data, started)
        return _finalize(data, is_new, sid)

    @app.get("/api/trace")
    async def get_trace(request: Request) -> JSONResponse:
        # Read-only and model-free: same session handling as `/`, burns no quota.
        sid, session, is_new = _ensure(request)
        if session is None:
            return JSONResponse(status_code=429, content={"detail": _IP_LIMIT_MSG})
        run_dir = _latest_trace_dir(sid)
        if run_dir is None:
            resp = JSONResponse(status_code=404, content={"detail": "No trace yet."})
        else:
            data = _trace_json(run_dir)
            log.info("trace: steps=%d models=%d", len(data["steps"]), len(data["models"]))
            resp = JSONResponse(data)
        if is_new:
            resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax", secure=settings.public_demo)
        return resp

    @app.get("/api/kedd-state")
    async def get_kedd_state(request: Request) -> JSONResponse:
        # Read-only and model-free, like /api/trace: burns no quota. `?fresh=1` bypasses
        # the 10 s cache — that is what the panel's retry button calls.
        sid, session, is_new = _ensure(request)
        if session is None:
            return JSONResponse(status_code=429, content={"detail": _IP_LIMIT_MSG})
        fresh = request.query_params.get("fresh") == "1"

        def _work() -> dict[str, Any]:
            with _lock(sid):
                return _kedd_state(session, fresh, kedd_cache)

        try:
            data = await asyncio.wait_for(run_in_threadpool(_work), KEDD_STATE_TIMEOUT_S)
        except TimeoutError:
            log.warning("kedd-state: probe timed out")
            data = _fallback_state("KEDD yavaş yanıt verdi, örnek salonu gösteriyorum.")
        log.info("kedd-state: state=%s", data["state"])
        resp = JSONResponse(data)
        if is_new:
            resp.set_cookie(COOKIE, sid, httponly=True, samesite="lax", secure=settings.public_demo)
        return resp

    @app.get("/api/media/{rel_path:path}", response_model=None)
    async def get_media(request: Request, rel_path: str):  # type: ignore[no-untyped-def]
        sid = request.cookies.get(COOKIE, "")
        if not sid or not _valid_sid(sid) or sid not in sessions:
            return JSONResponse(status_code=404, content={"detail": "Bulunamadı."})
        prefix = f"web/{sid}/"
        if not rel_path.startswith(prefix):
            return JSONResponse(status_code=404, content={"detail": "Bulunamadı."})
        low = rel_path.lower()
        if low.endswith(".png"):
            media_type = "image/png"
        elif low.endswith(".jpg") or low.endswith(".jpeg"):
            media_type = "image/jpeg"
        else:
            return JSONResponse(status_code=404, content={"detail": "Bulunamadı."})
        if ".." in Path(rel_path).parts:
            return JSONResponse(status_code=400, content={"detail": "Geçersiz yol."})
        base = settings.out_dir.resolve()
        full = (base / rel_path).resolve()
        try:
            full.relative_to(base / "web" / sid)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Geçersiz yol."})
        if not full.is_file():
            return JSONResponse(status_code=404, content={"detail": "Bulunamadı."})
        try:
            head = full.read_bytes()[:8]
        except OSError:
            return JSONResponse(status_code=404, content={"detail": "Bulunamadı."})
        if not _is_jpg_png(head):
            return JSONResponse(status_code=404, content={"detail": "Bulunamadı."})
        return FileResponse(str(full), media_type=media_type, headers=dict(_MEDIA_HEADERS))

    return app


def _check_uploads(uploads: list[tuple[str, bytes]], audio_content: bytes) -> str | None:
    """Public-demo upload policy. Returns the refusal message, or None when acceptable."""
    if len(uploads) > MAX_IMAGES:
        return _TOO_MANY_IMAGES
    for _name, content in uploads:
        if len(content) > MAX_IMAGE_BYTES:
            return _IMAGE_TOO_BIG
        if not _is_jpg_png(content):
            return _IMAGE_BAD_TYPE
    if len(audio_content) > MAX_AUDIO_BYTES:
        return _AUDIO_TOO_BIG
    return None


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
