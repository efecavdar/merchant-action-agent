"""Task 15, item 13: the public webhook container gets only the four WhatsApp secrets."""

import importlib
import sys
from pathlib import Path

import modal

SRC = (Path(__file__).resolve().parent.parent / "modal_app.py").read_text(encoding="utf-8")

_WEB_KEYS = {"META_APP_SECRET", "WA_VERIFY_TOKEN", "WA_PHONE_NUMBER_ID", "WA_ALLOWED_SENDERS"}


def test_t15_web_secret_is_scoped_and_built_locally():
    assert "web_secret" in SRC and "Secret.from_dict" in SRC
    for key in sorted(_WEB_KEYS):
        assert key in SRC
    # both secrets are built under the is_local() guard; containers get empty placeholders
    assert SRC.count("is_local()") >= 1
    assert "Secret.from_dict({})" in SRC
    assert "MODEL_API_KEY" in SRC  # the demo secret still carries the model key


def test_t15_web_function_uses_the_scoped_secret():
    at = SRC.index("def web():")
    deco = SRC[SRC.rindex("@app.function", 0, at):at]
    assert "secrets=[web_secret]" in deco
    assert "def worker(" in SRC and "def morning_brief(" in SRC  # untouched functions still exist


def test_t15_modal_app_imports_without_env_in_containers(monkeypatch):
    monkeypatch.setattr(modal, "is_local", lambda: False)
    monkeypatch.delitem(sys.modules, "modal_app", raising=False)
    mod = importlib.import_module("modal_app")
    assert mod.demo_secret is not None and mod.web_secret is not None
    monkeypatch.delitem(sys.modules, "modal_app", raising=False)
