"""Proactive morning brief: Çırak writes first so the owner never opens a dashboard.

The brief is just a fixed instruction run through the normal conversation path
(`core.handle`), so it behaves like any other message: either an answer or an
approval card that still needs the owner's `evet`.
"""

from __future__ import annotations

import logging
from typing import Callable

from . import core, worker
from .config import settings

log = logging.getLogger(__name__)

BRIEF_INSTRUCTION = ("Sabah özeti hazırla: bugünün randevuları, boş saatler, cevap bekleyen mesajlar, "
                     "dünkü ciro. Sonunda tek bir somut öneri sun; öneriyi uygulamak için onay "
                     "gerekiyorsa propose_actions ile plan kur.")

_MORNING = "Günaydın ☀️ "
_GREETINGS = ("günaydın", "gunaydin", "merhaba", "selam", "iyi sabahlar", "good morning")


def _starts_with_greeting(text: str) -> bool:
    stripped = text.strip().lower()
    while stripped and not stripped[0].isalnum():
        stripped = stripped[1:].lstrip()
    return stripped.startswith(_GREETINGS)


def build_morning_brief(session: core.Session) -> list[core.Reply]:
    """Run the brief instruction through `core.handle`; greet if the reply does not."""
    replies = core.handle(session, "text", BRIEF_INSTRUCTION)
    if replies and not _starts_with_greeting(replies[0].text):
        replies[0].text = _MORNING + replies[0].text
    return replies


def _is_window_error(exc: Exception) -> bool:
    """True when WhatsApp refused free text outside the 24h customer-service window."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return "131047" in str(exc)
    try:
        err = (resp.json() or {}).get("error", {})
    except Exception:  # noqa: BLE001 — an unreadable error body is simply not the window error
        return False
    return err.get("code") == 131047 or "131047" in str(err.get("message", ""))


def send_morning_briefs(load: Callable[[str], core.Session | None],
                        save: Callable[[str, core.Session], None]) -> dict[str, str]:
    """Brief every allow-listed sender. One sender's failure never stops the others."""
    results: dict[str, str] = {}
    for sender in sorted(settings.wa_allowed_senders):
        try:
            try:
                session = load(sender)
            except Exception:  # noqa: BLE001 — a stale pickle must not cancel the morning brief
                log.warning("could not load session for …%s; starting fresh", sender[-4:])
                session = None
            session = session or core.Session(sender=sender)
            try:
                errors = worker.deliver_replies(sender, build_morning_brief(session))
            finally:
                save(sender, session)
            if not errors:
                results[sender] = "sent"
            elif any(_is_window_error(e) for e in errors):
                log.info("brief to …%s skipped: outside the 24h WhatsApp window", sender[-4:])
                results[sender] = "skipped-24h"
            else:
                results[sender] = f"failed: {errors[0]}"
        except Exception as e:  # noqa: BLE001 — per-sender isolation for the whole loop
            log.exception("morning brief failed for …%s", sender[-4:])
            results[sender] = f"failed: {e}"
    return results
