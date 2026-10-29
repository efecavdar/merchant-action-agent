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
