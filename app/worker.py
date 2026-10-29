"""Processes one inbound WhatsApp message end to end: load session → media → core.handle → send replies."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from . import core
from .channels import whatsapp as wa
from .config import settings

log = logging.getLogger(__name__)


def deliver_replies(sender: str, replies: list[core.Reply]) -> list[Exception]:
    """Send every reply; a failed one is logged and does not stop the rest. Returns the errors."""
    errors: list[Exception] = []
    for r in replies:
        try:
            if r.image_path and Path(r.image_path).exists() and len(r.text) > wa.MAX_CAPTION:
                wa.send_image(sender, r.image_path)      # a card longer than a caption must arrive whole
                wa.send_text(sender, r.text)
            elif r.image_path and Path(r.image_path).exists():
                wa.send_image(sender, r.image_path, caption=r.text)
            elif r.text:
                wa.send_text(sender, r.text)
        except Exception as e:  # noqa: BLE001 — one failed reply must not stop the rest
            log.exception("reply delivery failed")
            errors.append(e)
    return errors


def process_inbound(msg: dict, load: Callable[[str], core.Session | None],
                    save: Callable[[str, core.Session], None]) -> None:
    m = wa.Inbound(**msg)
    try:
        session = load(m.sender)
    except Exception:  # noqa: BLE001 — a stale pickle after a deploy must not take the channel down
        log.warning("could not load session for …%s; starting fresh", m.sender[-4:])
        session = None
    session = session or core.Session(sender=m.sender)
    wa.mark_read(m.id)
    try:
        media_path = ""
        if m.media_id:
            media_path = str(wa.download_media(m.media_id, settings.out_dir / "inbox" / m.sender[-4:]))
        def notify(t: str) -> None:
            try:                      # progress notes are best effort; a failed one must not abort the job
                wa.send_text(m.sender, t)
            except Exception:  # noqa: BLE001
                log.warning("notify failed")       # notify text can carry the transcript

        replies = core.handle(session, m.kind, text=m.text, media_path=media_path, notify=notify,
                              sent_at=float(m.timestamp) if m.timestamp else None, async_delivery=True,
                              checkpoint=lambda: save(m.sender, session))
    except wa.MediaTooBig as e:
        replies = [core.Reply(str(e))]
    except Exception:
        log.exception("handling failed")
        replies = [core.Reply("Bir sorun çıktı, hiçbir şey yapılmadı. Tekrar dener misin?")]
    finally:
        save(m.sender, session)          # persist even on failure (e.g. a consumed approval stays consumed)
    errors = deliver_replies(m.sender, replies)
    core.mark_delivered(session, not errors)   # a card the owner never received must not stay approvable
    try:
        save(m.sender, session)
    except Exception:  # noqa: BLE001
        log.exception("could not save session after delivery")
