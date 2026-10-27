"""Processes one inbound WhatsApp message end to end: load session → media → core.handle → send replies."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from . import core
from .channels import whatsapp as wa
from .config import settings

log = logging.getLogger(__name__)


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
        replies = core.handle(session, m.kind, text=m.text, media_path=media_path,
                              notify=lambda t: wa.send_text(m.sender, t))
    except Exception:
        log.exception("handling failed")
        replies = [core.Reply("Bir sorun çıktı, hiçbir şey yapılmadı. Tekrar dener misin?")]
    finally:
        save(m.sender, session)          # persist even on failure (e.g. a consumed approval stays consumed)
    for r in replies:
        try:
            if r.image_path and Path(r.image_path).exists():
                wa.send_image(m.sender, r.image_path, caption=r.text)
            elif r.text:
                wa.send_text(m.sender, r.text)
        except Exception:
            log.exception("reply delivery failed")
