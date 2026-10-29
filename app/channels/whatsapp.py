"""WhatsApp Cloud API: webhook verification + parsing, media download, outbound messages.

Security:
  * every POST is checked against X-Hub-Signature-256 (HMAC-SHA256 of the raw body with the app secret)
  * only messages addressed to OUR phone_number_id are accepted
  * only senders on WA_ALLOWED_SENDERS can command the agent (everyone else is ignored, not answered)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import mimetypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from ..config import settings

log = logging.getLogger(__name__)

MAX_TEXT = 4096
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
_TOO_BIG_TR = "Medya 16 MB'tan büyük, daha küçük bir dosya dener misin?"


class MediaTooBig(ValueError):
    """Its message is Turkish and safe to show the owner."""


@dataclass
class Inbound:
    id: str                 # wamid, used for de-duplication (Meta redelivers on timeouts)
    sender: str             # E.164 without "+"
    kind: str               # text | audio | image | other
    text: str = ""          # body or image caption
    media_id: str = ""
    mime: str = ""
    timestamp: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# inbound
# --------------------------------------------------------------------------- #

def verify_signature(raw_body: bytes, header: str | None, app_secret: str | None = None) -> bool:
    secret = app_secret if app_secret is not None else settings.meta_app_secret
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_webhook(payload: dict[str, Any], phone_number_id: str | None = None) -> list[Inbound]:
    """Extract user messages addressed to our number. Status updates and other fields are dropped."""
    ours = phone_number_id if phone_number_id is not None else settings.wa_phone_number_id
    out: list[Inbound] = []
    if payload.get("object") != "whatsapp_business_account":
        return out
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            if (value.get("metadata") or {}).get("phone_number_id") != ours:
                log.warning("message for another phone_number_id ignored")
                continue
            for m in value.get("messages") or []:
                t = m.get("type")
                msg = Inbound(id=m.get("id", ""), sender=str(m.get("from", "")).lstrip("+"),
                              kind="other", timestamp=int(m.get("timestamp") or 0))
                if t == "text":
                    msg.kind, msg.text = "text", (m.get("text") or {}).get("body", "")
                elif t in ("audio", "voice"):
                    a = m.get(t) or {}
                    msg.kind, msg.media_id, msg.mime = "audio", a.get("id", ""), a.get("mime_type", "")
                elif t == "image":
                    im = m.get("image") or {}
                    msg.kind, msg.media_id, msg.mime = "image", im.get("id", ""), im.get("mime_type", "")
                    msg.text = im.get("caption", "")
                elif t == "button":
                    msg.kind, msg.text = "text", (m.get("button") or {}).get("text", "")
                elif t == "interactive":
                    it = m.get("interactive") or {}
                    reply = it.get("button_reply") or it.get("list_reply") or {}
                    msg.kind, msg.text = "text", reply.get("title", "")
                if msg.id and msg.sender:
                    out.append(msg)
    return out


def is_allowed(sender: str, allowed: frozenset[str] | None = None) -> bool:
    allowed = allowed if allowed is not None else settings.wa_allowed_senders
    return sender.lstrip("+") in allowed


# --------------------------------------------------------------------------- #
# Graph API calls
# --------------------------------------------------------------------------- #

def _auth() -> dict[str, str]:
    if not settings.wa_access_token:
        raise RuntimeError("WA_ACCESS_TOKEN is not set")
    return {"Authorization": f"Bearer {settings.wa_access_token}"}


def download_media(media_id: str, dest_dir: Path) -> Path:
    meta = requests.get(f"{settings.graph_api}/{media_id}", headers=_auth(), timeout=30)
    meta.raise_for_status()
    info = meta.json()
    with requests.get(info["url"], headers=_auth(), timeout=60, stream=True) as blob:   # closed on every path
        blob.raise_for_status()
        try:
            declared = int(blob.headers.get("Content-Length") or 0)
        except (ValueError, TypeError):
            declared = 0
        if declared > MAX_DOWNLOAD_BYTES:
            raise MediaTooBig(_TOO_BIG_TR)
        chunks: list[bytes] = []
        size = 0
        for chunk in blob.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise MediaTooBig(_TOO_BIG_TR)
            chunks.append(chunk)
    ext = mimetypes.guess_extension((info.get("mime_type") or "").split(";")[0]) or ".bin"
    if ext == ".oga":
        ext = ".ogg"
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{media_id}{ext}"
    path.write_bytes(b"".join(chunks))
    return path


def _graph_error_code(r: Any) -> Any:
    try:
        return r.json().get("error", {}).get("code", "?")
    except (ValueError, AttributeError):
        return "?"


def _post_message(body: dict[str, Any]) -> dict[str, Any]:
    r = requests.post(f"{settings.graph_api}/{settings.wa_phone_number_id}/messages",
                      headers=_auth(), json={"messaging_product": "whatsapp", **body}, timeout=30)
    if r.status_code >= 400:
        log.error("send failed status=%s code=%s", r.status_code, _graph_error_code(r))
    r.raise_for_status()
    return r.json()


def send_text(to: str, text: str) -> None:
    for i in range(0, len(text), MAX_TEXT):
        _post_message({"to": to, "type": "text", "text": {"body": text[i:i + MAX_TEXT], "preview_url": False}})


def send_image(to: str, path: str | Path, caption: str = "") -> None:
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    with open(path, "rb") as f:
        up = requests.post(f"{settings.graph_api}/{settings.wa_phone_number_id}/media", headers=_auth(),
                           data={"messaging_product": "whatsapp", "type": mime},
                           files={"file": (path.name, f, mime)}, timeout=60)
    up.raise_for_status()
    img: dict[str, Any] = {"id": up.json()["id"]}
    if caption:
        img["caption"] = caption[:1024]
    _post_message({"to": to, "type": "image", "image": img})


def mark_read(message_id: str) -> None:
    """Blue ticks + typing indicator while the agent works. Best effort."""
    try:
        _post_message({"status": "read", "message_id": message_id, "typing_indicator": {"type": "text"}})
    except Exception as e:  # noqa: BLE001 — cosmetic, never block the flow on it
        log.info("mark_read failed: %s", e)
