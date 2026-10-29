"""Offline tests must not depend on a real key or a local .env."""

import os

os.environ.setdefault("MODEL_API_KEY", "test-key-offline")
