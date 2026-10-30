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
from datetime import date
from pathlib import Path
from typing import Any, Callable

from . import agent, approval, executor, i18n, pipeline, report
from .config import settings
from .tools.backend import Backend, KeddBackend, get_backend

log = logging.getLogger(__name__)

# Only explicit approval words. "ok", "tamam", "olur", "e" are everyday chat and must never run a plan.
YES = {"evet", "onay", "onayla", "onaylıyorum", "evet onaylıyorum", "evet gönder", "yes"}
NO = {"hayır", "hayir", "h", "iptal", "vazgeç", "vazgec", "no", "n", "dur"}
PENDING_TTL_S = 2 * 3600
IMAGES_TTL_S = 30 * 60
HISTORY_KEEP = 10
HISTORY_TTL_S = 2 * 3600
HISTORY_REPLY_CHARS = 600

HELP = i18n.t("help", "tr")  # default-language greeting; handle() serves it in Session.lang


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
    lang: str = "tr"  # owner language, re-detected from every text message / transcript

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


def _maybe_set_lang(session: Session, text: str) -> None:
    """Follow the owner's language. Bare approval words and /commands are control tokens, not prose."""
    if not text.strip():
        return
    if _norm(text) in YES or _norm(text) in NO or _norm(text).startswith("/"):
        return
    session.lang = i18n.detect_lang(text, session.lang)


def whatsapp_session(sender: str) -> Session:
    """A WhatsApp owner starts on their own KEDD business: the gateway resolves it from their number."""
    if settings.kedd_api_url and settings.kedd_agent_secret:
        return Session(sender=sender, business="kedd")
    return Session(sender=sender)


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


# A summary of the inbox quotes customers. Keeping it would hand customer text to the next run's planner, which
# did not read the inbox and so is not quarantined: remember only that it happened.
_INBOX_REDACTED = "[Gelen kutusu gösterildi; müşteri metinleri hafızaya alınmadı.]"


def _remember_run(session: Session, replies: list[Reply], now: float, inbox_read: bool) -> None:
    if inbox_read:
        _history_add(session, "cirak", _INBOX_REDACTED, now)
    else:
        _remember_replies(session, replies, now)


def _new_run_dir(session: Session) -> Path:
    d = settings.out_dir / "runs" / session.sender[-4:] / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def handle(session: Session, kind: str, text: str = "", media_path: str = "",
           notify: Notify | None = None, *, sent_at: float | None = None, async_delivery: bool = False,
           checkpoint: Callable[[], None] | None = None) -> list[Reply]:
    """Process one inbound message. Mutates `session`; returns replies to deliver.

    async_delivery: the channel delivers replies after this returns (WhatsApp). A new approval card then stays
        unapprovable until the channel calls `mark_delivered(session, True)`; if delivery fails the card is dropped.
    sent_at: when the owner sent this message (channel timestamp). An approval sent before the card was shown
        is refused — the owner cannot approve a card they have not seen.
    checkpoint: persists the session; called after an approval is consumed and BEFORE anything executes, so a
        crash mid-execution can never leave the card approvable again."""
    notify = notify or (lambda _msg: None)
    now = time.time()
    _history_prune(session, now)
    if session.pending and now - session.pending["at"] > PENDING_TTL_S:
        session.pending = None
    if session.images and now - session.images_at > IMAGES_TTL_S:
        session.images = []

    t = _norm(text)
    if kind in ("text", "image"):
        _maybe_set_lang(session, text)
    lang = session.lang

    # -- demo commands ---------------------------------------------------------
    if kind == "text" and t in {"/butik", "/salon"}:
        session.business, session.backend, session.pending, session.images = t[1:], None, None, []
        session.history = []
        name = session.get_backend().store_profile().get("business_name", t[1:])
        return [Reply(i18n.t("business.switched", lang).format(name=name))]
    if kind == "text" and t == "/kedd":
        try:
            backend = get_backend("kedd", sender=session.sender)
        except RuntimeError:
            return [Reply(i18n.t("kedd.off", lang))]
        session.business, session.backend, session.pending, session.images = "kedd", backend, None, []
        session.history = []
        try:
            name = backend.store_profile().get("business_name", "")
        except Exception:  # noqa: BLE001 — gateway unreachable; still switched, name stays unknown
            name = ""
        if name:
            return [Reply(i18n.t("kedd.switched_named", lang).format(name=name))]
        return [Reply(i18n.t("kedd.switched", lang))]
    if kind == "text" and t in {"/sifirla", "/reset"}:
        session.backend, session.pending, session.images = None, None, []
        session.history = []
        return [Reply(i18n.t("reset.done", lang))]
    if kind == "text" and t in {"/yardim", "/help", "yardım", "merhaba", "selam"} and not session.pending:
        session.greeted = True
        return [Reply(i18n.t("help", lang))]

    # -- approval --------------------------------------------------------------
    if session.pending and kind == "text" and t in YES:
        _history_add(session, "owner", text, now)
        shown_at = session.pending.get("shown_at", session.pending["at"])   # key missing = card from an older deploy
        if shown_at is None or (sent_at is not None and sent_at < shown_at):
            replies = [Reply(i18n.t("approval.race", lang))]
            _remember_replies(session, replies, now)
            return replies
        replies = _execute_pending(session, notify, checkpoint)
        _remember_replies(session, replies, now)
        return replies
    if session.pending and kind == "text" and t in NO:
        _history_add(session, "owner", text, now)
        session.pending = None
        replies = [Reply(i18n.t("approval.cancelled", lang))]
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
                unit = i18n.t("photo.unit_one", lang)
                replies = [Reply(i18n.t("photo.received", lang).format(n=n, unit=unit))]
                _remember_replies(session, replies, now)
                return replies
            return []
        kind = "text"  # the caption is the instruction

    if kind not in ("text", "audio"):
        return [Reply(i18n.t("error.unreadable", lang))]

    # -- new instruction → plan ------------------------------------------------
    replies: list[Reply] = []
    if session.pending:
        session.pending = None
        replies.append(Reply(i18n.t("plan.superseded", lang)))
    if not session.greeted:
        session.greeted = True
    if session.images:
        unit = i18n.t("photo.unit_one" if len(session.images) == 1 else "photo.unit_many", lang)
        notify(i18n.t("notify.looking", lang)
               + i18n.t("notify.photo_suffix", lang).format(n=len(session.images), unit=unit))
    else:
        notify(i18n.t("notify.looking", lang))

    run_dir = _new_run_dir(session)
    backend = session.get_backend()
    try:
        ctx = pipeline.understand_media(
            text if kind == "text" else None,
            media_path if kind == "audio" else None,
            list(session.images), out_dir=run_dir,
        )
    except ValueError as e:              # the pipeline's ValueErrors are written for the owner (Turkish, actionable)
        replies.append(Reply(str(e)))
        _remember_replies(session, replies, now)
        return replies
    session.images = []
    if ctx.instruction_source == "voice":
        _maybe_set_lang(session, ctx.instruction)
        lang = session.lang
        notify(i18n.t("notify.voice", lang).format(text=ctx.instruction))
    # The planner sees the conversation so far, not the message it is about to answer.
    ctx.history = [dict(h) for h in session.history]
    _history_add(session, "owner", ctx.instruction if kind == "audio" else text, now)

    run = agent.plan(ctx, backend, lang=lang)
    (run_dir / "tool_log.json").write_text(json.dumps(run.tool_log, ensure_ascii=False, indent=2, default=str))
    if run.answer:                       # a question, not a job: answer and keep nothing pending
        log.info("answered without actions: %d turns, usage %s", run.turns, run.usage)
        replies = replies + [Reply(run.answer)]
        _remember_run(session, replies, now, getattr(run, "inbox_read", False))
        return replies
    seg_counts = {e["result"].get("segment_id"): e["result"].get("count")
                  for e in run.tool_log if e.get("tool") == "find_customers" and isinstance(e.get("result"), dict)}
    executor.prepare_social_previews(run.proposal, ctx, run_dir)
    card = approval.render_card(run.proposal, ctx, seg_counts, approval.card_facts(run.tool_log),
                                lang=lang)
    (run_dir / "proposal.json").write_text(json.dumps(run.proposal, ensure_ascii=False, indent=2))
    (run_dir / "approval_card.txt").write_text(card)
    session.pending = {"proposal": run.proposal, "ctx": ctx, "seg_counts": seg_counts,
                       "run_dir": str(run_dir), "at": now, "usage": run.usage,
                       "shown_at": None if async_delivery else time.time()}
    log.info("plan ready: %d turns, usage %s", run.turns, run.usage)
    preview = next((a.get("_preview_path") or "" for a in run.proposal["actions"]
                    if a.get("type") == "publish_social_post"), "")
    replies = replies + [Reply(card, image_path=preview)]
    _remember_run(session, replies, now, getattr(run, "inbox_read", False))
    return replies


def mark_delivered(session: Session, ok: bool) -> None:
    """Channel callback after delivering replies. An undelivered card is dropped, never left approvable."""
    if session.pending and session.pending.get("shown_at", 0) is None:
        if ok:
            session.pending["shown_at"] = time.time()
        else:
            log.warning("approval card could not be delivered; dropping it")
            session.pending = None


def _execute_pending(session: Session, notify: Notify,
                     checkpoint: Callable[[], None] | None = None) -> list[Reply]:
    p, session.pending = session.pending, None      # one approval, one execution — even if this crashes
    if checkpoint:
        checkpoint()                                # persist "consumed" before any side effect
    lang = session.lang
    notify(i18n.t("notify.approved", lang))
    backend = session.get_backend()
    run_dir = Path(p["run_dir"])
    world_today = getattr(backend, "today", None)
    today_iso = world_today.isoformat() if isinstance(world_today, date) else date.today().isoformat()

    def _announce(event: str, i: int, n: int, step: dict[str, Any] | None) -> None:
        line = i18n.render_progress(event, i, n, step, lang, today=today_iso)
        if line:
            notify(line)

    steps = executor.execute(p["proposal"], p["ctx"], backend, out_dir=run_dir, on_step=_announce)
    # From here on things HAVE happened. A failing report must never turn into "nothing was done".
    try:
        rep = report.build_report(steps, backend)
        (run_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        summary = report.summarize_for_merchant(rep, lang=lang)
    except Exception:  # noqa: BLE001
        log.exception("report failed after execution; falling back to the step list")
        summary = _plain_summary(steps, lang)
    if isinstance(backend, KeddBackend):
        summary += "\n" + i18n.t("kedd.panel_note", lang)
    failed = [s for s in steps if s["status"] != "ok"]
    if failed:
        summary += i18n.t("report.failed", lang).format(err=str(failed[0]["result"])[:200])
    visual = next((s["result"]["visual_path"] for s in steps
                   if s["action"] == "generate_visual" and s["status"] == "ok"), "")
    return [Reply(summary, image_path=visual)]


def _plain_summary(steps: list[dict[str, Any]], lang: str = "tr") -> str:
    """Deterministic, model-free account of what ran. Used when the normal report cannot be built."""
    lines = [i18n.t("report.fallback_title", lang)]
    for s in steps:
        r = s["result"] if isinstance(s["result"], dict) else {}
        detail = r.get("name") or r.get("label") or (f"%{r['percent']}" if "percent" in r else "") or (
            i18n.t("report.recipients", lang).format(n=r["recipients"]) if "recipients" in r else "")
        mark = "✅" if s["status"] == "ok" else "⚠️"
        lines.append(f"{mark} {i18n.t(f'step.{s['action']}', lang, s['action'])}"
                     + (f": {detail}" if detail else ""))
    return "\n".join(lines)
