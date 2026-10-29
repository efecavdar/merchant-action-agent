"""HTTP surface: WhatsApp webhook. The heavy work is handed to `dispatch` so Meta gets a 200 immediately
(Meta retries slow webhooks, and planning takes ~30 s).

Deployed on Modal by modal_app.py; runnable locally with `uvicorn app.server:local_app`.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from .channels import whatsapp as wa
from .config import settings

log = logging.getLogger(__name__)

Dispatch = Callable[[dict], None]          # receives Inbound.to_dict()
SeenCheck = Callable[[str], bool]          # returns True the FIRST time an id is seen
Unmark = Callable[[str], None]             # forget an id again so a redelivery is processed


def create_app(dispatch: Dispatch, first_time: SeenCheck,
               unmark: Unmark | None = None) -> FastAPI:
    api = FastAPI(title="Çırak webhook", docs_url=None, redoc_url=None, openapi_url=None)

    @api.get("/health")
    def health():
        return {"ok": True}

    @api.get("/webhook")
    def verify(mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"),
               challenge: str = Query("", alias="hub.challenge")):
        if mode == "subscribe" and settings.wa_verify_token and token == settings.wa_verify_token:
            return PlainTextResponse(challenge)
        raise HTTPException(status_code=403)

    @api.post("/webhook")
    async def receive(request: Request):
        raw = await request.body()
        if not wa.verify_signature(raw, request.headers.get("x-hub-signature-256")):
            log.warning("bad webhook signature")
            raise HTTPException(status_code=401)
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400)
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400)
        for msg in wa.parse_webhook(payload):
            if not wa.is_allowed(msg.sender):
                log.warning("ignored message from non-allowed sender …%s", msg.sender[-4:])
                continue
            # dispatch / first_time may be blocking (Modal Dict + spawn) → keep them off the event loop
            if not await run_in_threadpool(first_time, msg.id):
                log.info("duplicate delivery %s ignored", msg.id)
                continue
            try:
                await run_in_threadpool(dispatch, msg.to_dict())
            except Exception:
                # The job never started: un-mark the id and fail loud, so Meta's redelivery
                # is treated as new instead of dropped as a duplicate.
                log.exception("dispatch failed for %s", msg.id)
                if unmark is not None:
                    await run_in_threadpool(unmark, msg.id)
                raise HTTPException(status_code=500)
        return {"ok": True}

    return api


def _local_app() -> FastAPI:
    """Single-process dev server: in-memory sessions, background thread per message."""
    import threading

    from .worker import process_inbound

    seen: set[str] = set()
    sessions: dict = {}
    lock = threading.Lock()

    def first_time(mid: str) -> bool:
        with lock:
            if mid in seen:
                return False
            seen.add(mid)
            return True

    def unmark(mid: str) -> None:
        with lock:
            seen.discard(mid)

    def dispatch(msg: dict) -> None:
        def run():
            with lock:   # serialise per process; fine for a demo
                process_inbound(msg, sessions.get, sessions.__setitem__)
        threading.Thread(target=run, daemon=True).start()

    return create_app(dispatch, first_time, unmark=unmark)


def __getattr__(name: str):
    if name == "local_app":
        return _local_app()
    raise AttributeError(name)
