"""Modal deployment: public WhatsApp webhook + a serialised worker.

    modal deploy modal_app.py          # prints the https://…modal.run URL for the webhook
    modal app logs cirak                # live logs

Secrets come from the local .env at deploy time (modal.Secret.from_dotenv); nothing is baked into the image.
"""

from __future__ import annotations

import modal

app = modal.App("cirak")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install_from_requirements("requirements.txt")
    .pip_install("fastapi[standard]")
    .env({"DATA_DIR": "/data", "OUT_DIR": "/data/out"})
    .add_local_python_source("app")
)

secret = modal.Secret.from_dotenv(__file__)
volume = modal.Volume.from_name("cirak-data", create_if_missing=True)
sessions = modal.Dict.from_name("cirak-sessions", create_if_missing=True)
seen = modal.Dict.from_name("cirak-seen", create_if_missing=True)


# One container, one message at a time: approvals and sessions can never race.
@app.function(image=image, secrets=[secret], volumes={"/data": volume}, timeout=600, max_containers=1)
def worker(msg: dict) -> None:
    import logging

    from app.worker import process_inbound

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    volume.reload()
    try:
        process_inbound(msg, sessions.get, sessions.__setitem__)
    finally:
        volume.commit()


# 06:00 UTC = 09:00 Türkiye. Manual trigger for the demo: modal run modal_app.py::morning_brief
@app.function(image=image, secrets=[secret], volumes={"/data": volume},
              schedule=modal.Cron("0 6 * * *"), max_containers=1)
def morning_brief() -> dict[str, str]:
    import logging

    from app.brief import send_morning_briefs

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    volume.reload()
    try:
        return send_morning_briefs(sessions.get, sessions.__setitem__)
    finally:
        volume.commit()


@app.function(image=image, secrets=[secret], min_containers=1)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    import logging

    from app.server import create_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    def first_time(mid: str) -> bool:
        return bool(seen.put(mid, 1, skip_if_exists=True))

    return create_app(dispatch=lambda m: worker.spawn(m), first_time=first_time)
