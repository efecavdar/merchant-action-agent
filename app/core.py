"""Channel-independent conversation core.

One merchant = one Session. Every channel (WhatsApp webhook, web UI, tests) turns an incoming
message into `handle(session, kind, ...)` and delivers the returned replies.

    photos  ─► buffered in the session ("📷 aldım")
    voice / text instruction ─► pipeline + agent.plan ─► approval card (pending)
    "evet"  ─► executor ─► report (+ campaign visual)
    "hayır" ─► nothing runs

The approval is bound to the session, i.e. to the sender who received the card.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable

from . import agent, approval, executor, pipeline, report
from .config import settings
from .tools.backend import Backend, get_backend

log = logging.getLogger(__name__)

YES = {"evet", "e", "onay", "onaylıyorum", "tamam", "ok", "olur", "yes", "y", "gönder", "evet gönder"}
NO = {"hayır", "hayir", "h", "iptal", "vazgeç", "vazgec", "no", "n", "dur"}
PENDING_TTL_S = 2 * 3600
IMAGES_TTL_S = 30 * 60
HISTORY_KEEP = 10
HISTORY_TTL_S = 2 * 3600
HISTORY_REPLY_CHARS = 600

HELP = ("Merhaba, ben Çırak 👋 Sesli not ya da yazıyla ne istediğini söyle, istersen önce ürün fotoğrafı at.\n"
        "Örn: \"Bunları ekle, tanesi 850 lira, hafta sonu %15 indirim, uzun süredir gelmeyenlere haber ver.\"\n"
        "Hiçbir şey senin *evet*'in olmadan yapılmaz.\n"
        "Demo: /butik, /salon veya /kedd ile işletme değiştir, /sifirla ile baştan başla.")


@dataclass
class Reply:
    text: str = ""
    image_path: str = ""


@dataclass
class Session:
    sender: str
    business: str = field(default_factory=lambda: settings.mock_business)
    backend: Backend | None = None
    images: list[str] = field(default_factory=list)
    images_at: float = 0.0
    pending: dict[str, Any] | None = None       # {"proposal", "ctx", "seg_counts", "run_dir", "at"}
    greeted: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)  # {"role": "owner"|"cirak", "text", "at"}

    def __setstate__(self, state: dict[str, Any]) -> None:
        # A session pickled before a field existed (e.g. `history`) unpickles without it.
        for f in fields(self):
            if f.name not in state:
                if f.default_factory is not MISSING:
                    state[f.name] = f.default_factory()  # type: ignore[misc]
                elif f.default is not MISSING:
                    state[f.name] = f.default
        self.__dict__.update(state)

    def get_backend(self) -> Backend:
        # A session pickled by an older deploy may hold a backend whose shape no longer matches the code.
        # Re-seed it rather than crash halfway through an approved plan.
        if self.backend is not None and getattr(self.backend, "_schema", None) != getattr(
                type(self.backend), "SCHEMA", None):
            log.warning("stale backend in session (schema changed); re-seeding demo data")
            self.backend = None
        if self.backend is None:
            self.backend = get_backend(self.business, sender=self.sender)
        return self.backend


Notify = Callable[[str], None]


def _norm(text: str) -> str:
    return text.strip().lower().strip(".!?,👍 ")


def _history_prune(session: Session, now: float) -> None:
    """Drop items older than HISTORY_TTL_S; keep the newest HISTORY_KEEP."""
    session.history = [h for h in session.history if now - h.get("at", now) <= HISTORY_TTL_S][-HISTORY_KEEP:]


def _history_add(session: Session, role: str, text: str, now: float) -> None:
    if not text:
        return
    if role == "cirak":
        text = text[:HISTORY_REPLY_CHARS]
    session.history.append({"role": role, "text": text, "at": now})
    _history_prune(session, now)


def _remember_replies(session: Session, replies: list[Reply], now: float) -> None:
    for r in replies:
        _history_add(session, "cirak", r.text, now)


def _new_run_dir(session: Session) -> Path:
    d = settings.out_dir / "runs" / session.sender[-4:] / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def handle(session: Session, kind: str, text: str = "", media_path: str = "",
           notify: Notify | None = None) -> list[Reply]:
    """Process one inbound message. Mutates `session`; returns replies to deliver."""
    notify = notify or (lambda _msg: None)
    now = time.time()
    _history_prune(session, now)
    if session.pending and now - session.pending["at"] > PENDING_TTL_S:
        session.pending = None
    if session.images and now - session.images_at > IMAGES_TTL_S:
        session.images = []

    t = _norm(text)

    # -- demo commands ---------------------------------------------------------
    if kind == "text" and t in {"/butik", "/salon"}:
        session.business, session.backend, session.pending, session.images = t[1:], None, None, []
        session.history = []
        name = session.get_backend().store_profile().get("business_name", t[1:])
        return [Reply(f"Tamam, artık *{name}* (demo) için çalışıyorum.")]
    if kind == "text" and t == "/kedd":
        try:
            backend = get_backend("kedd", sender=session.sender)
        except RuntimeError:
            return [Reply("KEDD bağlantısı bu ortamda kapalı.")]
        session.business, session.backend, session.pending, session.images = "kedd", backend, None, []
        session.history = []
        try:
            name = backend.store_profile().get("business_name", "")
        except Exception:  # noqa: BLE001 — gateway unreachable; still switched, name stays unknown
            name = ""
        if name:
            return [Reply(f"Tamam, artık KEDD'deki işletmen (*{name}*) için çalışıyorum.")]
        return [Reply("Tamam, artık KEDD'deki işletmen için çalışıyorum.")]
    if kind == "text" and t in {"/sifirla", "/reset"}:
        session.backend, session.pending, session.images = None, None, []
        session.history = []
        return [Reply("Sıfırladım. Demo verisi baştan yüklendi.")]
    if kind == "text" and t in {"/yardim", "/help", "yardım", "merhaba", "selam"} and not session.pending:
        session.greeted = True
        return [Reply(HELP)]

    # -- approval --------------------------------------------------------------
    if session.pending and kind == "text" and t in YES:
        _history_add(session, "owner", text, now)
        replies = _execute_pending(session, notify)
        _remember_replies(session, replies, now)
        return replies
    if session.pending and kind == "text" and t in NO:
        _history_add(session, "owner", text, now)
        session.pending = None
        replies = [Reply("Tamam, hiçbir şey yapmadım. Planı iptal ettim.")]
        _remember_replies(session, replies, now)
        return replies

    # -- photos are buffered until an instruction arrives ------------------------
    if kind == "image":
        session.images.append(media_path)
        session.images_at = now
        if not text:   # no caption → wait for the instruction
            n = len(session.images)
            _history_add(session, "owner", f"[{n} fotoğraf]", now)
            if n == 1:
                replies = [Reply(f"📷 {n} fotoğraf aldım. Ne yapayım? Sesli not ya da mesajla söyle.")]
                _remember_replies(session, replies, now)
                return replies
            return []
        kind = "text"  # the caption is the instruction

    if kind not in ("text", "audio"):
        return [Reply("Bunu okuyamadım. Yazı, sesli not ya da fotoğraf gönderebilirsin.")]

    # -- new instruction → plan ------------------------------------------------
    replies: list[Reply] = []
    if session.pending:
        session.pending = None
        replies.append(Reply("Önceki planı iptal ettim, yeni isteğine bakıyorum."))
    if not session.greeted:
        session.greeted = True
    notify("⏳ Bakıyorum…" + (f" ({len(session.images)} fotoğraf)" if session.images else ""))

    run_dir = _new_run_dir(session)
    backend = session.get_backend()
    ctx = pipeline.understand_media(
        text if kind == "text" else None,
        media_path if kind == "audio" else None,
        list(session.images), out_dir=run_dir,
    )
    session.images = []
    if ctx.instruction_source == "voice":
        notify(f"🎙️ Anladığım: “{ctx.instruction}”")
    # The planner sees the conversation so far, not the message it is about to answer.
    ctx.history = [dict(h) for h in session.history]
    _history_add(session, "owner", ctx.instruction if kind == "audio" else text, now)

    run = agent.plan(ctx, backend)
    (run_dir / "tool_log.json").write_text(json.dumps(run.tool_log, ensure_ascii=False, indent=2, default=str))
    if run.answer:                       # a question, not a job: answer and keep nothing pending
        log.info("answered without actions: %d turns, usage %s", run.turns, run.usage)
        replies = replies + [Reply(run.answer)]
        _remember_replies(session, replies, now)
        return replies
    seg_counts = {e["result"].get("segment_id"): e["result"].get("count")
                  for e in run.tool_log if e.get("tool") == "find_customers" and isinstance(e.get("result"), dict)}
    card = approval.render_card(run.proposal, ctx, seg_counts, approval.card_facts(run.tool_log))
    (run_dir / "proposal.json").write_text(json.dumps(run.proposal, ensure_ascii=False, indent=2))
    (run_dir / "approval_card.txt").write_text(card)
    session.pending = {"proposal": run.proposal, "ctx": ctx, "seg_counts": seg_counts,
                       "run_dir": str(run_dir), "at": now, "usage": run.usage}
    log.info("plan ready: %d turns, usage %s", run.turns, run.usage)
    replies = replies + [Reply(card)]
    _remember_replies(session, replies, now)
    return replies


def _execute_pending(session: Session, notify: Notify) -> list[Reply]:
    p, session.pending = session.pending, None      # one approval, one execution — even if this crashes
    notify("✅ Onaylandı, uyguluyorum…")
    backend = session.get_backend()
    run_dir = Path(p["run_dir"])
    steps = executor.execute(p["proposal"], p["ctx"], backend, out_dir=run_dir)
    # From here on things HAVE happened. A failing report must never turn into "nothing was done".
    try:
        rep = report.build_report(steps, backend)
        (run_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        summary = report.summarize_for_merchant(rep)
    except Exception:  # noqa: BLE001
        log.exception("report failed after execution; falling back to the step list")
        summary = _plain_summary(steps)
    failed = [s for s in steps if s["status"] != "ok"]
    if failed:
        summary += "\n\n⚠️ Bir adım başarısız oldu, sonrasını güvenlik için durdurdum: " + str(failed[0]["result"])[:200]
    visual = next((s["result"]["visual_path"] for s in steps
                   if s["action"] == "generate_visual" and s["status"] == "ok"), "")
    return [Reply(summary, image_path=visual)]


_STEP_TR = {"add_catalog_item": "Kataloğa eklendi", "create_discount": "İndirim tanımlandı",
            "generate_visual": "Kampanya görseli hazırlandı", "send_campaign": "Kampanya gönderildi",
            "create_appointment": "Randevu oluşturuldu", "reschedule_appointment": "Randevu taşındı",
            "cancel_appointment": "Randevu iptal edildi", "apply_bulk_price": "Toplu fiyat güncellendi",
            "adjust_loyalty": "Puan güncellendi", "ads_action": "Reklam güncellendi"}


def _plain_summary(steps: list[dict[str, Any]]) -> str:
    """Deterministic, model-free account of what ran. Used when the normal report cannot be built."""
    lines = ["Yapılanlar:"]
    for s in steps:
        r = s["result"] if isinstance(s["result"], dict) else {}
        detail = r.get("name") or r.get("label") or (f"%{r['percent']}" if "percent" in r else "") or (
            f"{r['recipients']} kişi" if "recipients" in r else "")
        mark = "✅" if s["status"] == "ok" else "⚠️"
        lines.append(f"{mark} {_STEP_TR.get(s['action'], s['action'])}" + (f": {detail}" if detail else ""))
    return "\n".join(lines)
