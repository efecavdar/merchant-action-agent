"""Offline tests must not depend on a real key or a local .env."""

import os

# load_dotenv() never overrides variables that already exist, so pinning them here keeps a developer's
# real .env (live KEDD keys, WhatsApp token) out of the test process: no test can reach a live system.
_OFFLINE = {
    "MODEL_API_KEY": "test-key-offline",
    "BACKEND": "mock", "PUBLIC_DEMO": "",
    "KEDD_API_URL": "", "KEDD_AGENT_SECRET": "", "KEDD_DEMO_SECRET": "", "KEDD_DEMO_PHONE": "",
    "MERCHANT_PHONE": "", "META_APP_SECRET": "", "WA_ACCESS_TOKEN": "", "WA_VERIFY_TOKEN": "",
    "WA_PHONE_NUMBER_ID": "", "WA_ALLOWED_SENDERS": "",
}
for _k, _v in _OFFLINE.items():
    os.environ[_k] = _v

import time  # noqa: E402

import pytest  # noqa: E402

from app.tools.backend import KeddBackend  # noqa: E402  (env above is pinned first)

REAL_UNUSED_TIMESTAMP = KeddBackend._unused_timestamp


@pytest.fixture(autouse=True)
def _no_replay_wait(monkeypatch):
    # Fake gateways have no replay guard; skip the real one-second wait (tested on its own).
    monkeypatch.setattr(KeddBackend, "_unused_timestamp", lambda self, signed: str(int(time.time())))
