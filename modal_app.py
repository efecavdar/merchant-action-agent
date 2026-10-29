"""Modal deployment: public WhatsApp webhook + a serialised worker.

    modal deploy modal_app.py          # prints the https://…modal.run URL for the webhook
    modal app logs cirak                # live logs

Secrets come from the local .env at deploy time (modal.Secret.from_dotenv); nothing is baked into the image.
"""

from __future__ import annotations

from pathlib import Path

import modal

app = modal.App("cirak")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install_from_requirements("requirements.txt")
    .pip_install("fastapi[standard]")
    .env({"DATA_DIR": "/data", "OUT_DIR": "/data/out"})
    .add_local_python_source("app")
    .add_local_dir("app/static", remote_path="/root/app/static")   # web UI assets (python_source copies .py only)
)

secret = modal.Secret.from_dotenv(__file__)
volume = modal.Volume.from_name("cirak-data", create_if_missing=True)
sessions = modal.Dict.from_name("cirak-sessions", create_if_missing=True)
seen = modal.Dict.from_name("cirak-seen", create_if_missing=True)


def _read_local_env(want: set[str]) -> dict[str, str]:
    """Values from the deploy machine's .env, parsed by python-dotenv (inline comments, quotes, export).
    Call only under modal.is_local()."""
    from dotenv import dotenv_values

    env = dotenv_values(Path(__file__).with_name(".env"))
    return {k: v for k in want if (v := (env.get(k) or "").strip())}


_WEB_KEYS = {"META_APP_SECRET", "WA_VERIFY_TOKEN", "WA_PHONE_NUMBER_ID", "WA_ALLOWED_SENDERS"}

if modal.is_local():
    # The public webhook only verifies signatures and filters senders: it gets the four
    # WhatsApp secrets and nothing else (no model key, no KEDD secrets, no write token).
    web_secret = modal.Secret.from_dict(_read_local_env(_WEB_KEYS))
else:
    web_secret = modal.Secret.from_dict({})


# One container, one message at a time: approvals and sessions can never race.
@app.function(image=image, secrets=[secret], volumes={"/data": volume}, timeout=600, max_containers=1)
def worker(msg: dict) -> dict[str, str] | None:
    import logging

    from app.worker import process_inbound

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    volume.reload()
    try:
        if msg.get("brief"):           # internal job from morning_brief; webhook payloads never carry this key
            from app.brief import send_morning_briefs
            return send_morning_briefs(sessions.get, sessions.__setitem__)
        process_inbound(msg, sessions.get, sessions.__setitem__)
    finally:
        volume.commit()


# 06:00 UTC = 09:00 Türkiye. Manual trigger for the demo: modal run modal_app.py::morning_brief
# The brief runs INSIDE `worker`, so it is serialised with inbound messages. Running it in its own container
# could write back a stale session copy and revive an approval card the owner just consumed.
@app.function(image=image, secrets=[secret], schedule=modal.Cron("0 6 * * *"))
def morning_brief() -> dict[str, str]:
    return worker.remote({"brief": True})


@app.function(image=image, secrets=[web_secret], min_containers=1)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    import logging

    from app.server import create_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    def first_time(mid: str) -> bool:
        return bool(seen.put(mid, 1, skip_if_exists=True))

    def unmark(mid: str) -> None:      # dispatch failed: let Meta's redelivery through
        seen.pop(mid, None)

    return create_app(dispatch=lambda m: worker.spawn(m), first_time=first_time, unmark=unmark)
# Public judges demo: mock businesses only. The secret carries ONLY the model key plus the two
# forced flags — from_dotenv would drag KEDD and WhatsApp secrets into this container, so the
# single key is parsed out of the local .env by hand instead. The .env read happens only on the
# deploy machine: inside containers is_local() is False and the secret stays an empty placeholder
# (the real values arrive via the deployed Secret).
if modal.is_local():
    _demo_env = {"PUBLIC_DEMO": "true", "BACKEND": "mock", **_read_local_env({"MODEL_API_KEY"})}
    if "MODEL_API_KEY" not in _demo_env:
        raise RuntimeError("MODEL_API_KEY not found in the local .env; refusing to deploy the public demo.")
    demo_secret = modal.Secret.from_dict(_demo_env)
else:
    demo_secret = modal.Secret.from_dict({})

demo_counts = modal.Dict.from_name("cirak-demo-counts", create_if_missing=True)


@app.function(image=image, secrets=[demo_secret], timeout=600, max_containers=1)
@modal.asgi_app()
def demo():
    """Public web UI for judges: mock only, rate-limited, no volume (ephemeral disk)."""
    import logging
    import os
    import tempfile

    from app.channels.web import create_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # No volume: uploads, runs and generated visuals live on ephemeral disk. Set before the
    # app import — Settings reads the env once, at import time.
    os.environ["DATA_DIR"] = os.environ["OUT_DIR"] = tempfile.mkdtemp(prefix="cirak-demo-")
    return create_app(daily_counts=demo_counts)
