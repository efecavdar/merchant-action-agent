"""Task 24: the live demo business is KEDD itself — KEDD quick prompts when live,
mock boutique/salon chips otherwise, and a vertical-neutral system prompt."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import agent
from app.channels import web as webchan
from app.config import settings
from app.tools.backend import KeddBackend

HARNESS = Path(__file__).resolve().parent / "fixtures" / "chips_harness.js"
APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"

# The live KEDD business is driven by the guided tour (app.js TOUR); the phone keeps one pointer to it.
TOUR_TEXTS = [
    "Good morning! What's waiting for me today?",
    "Add this to the catalog as a new product. NFC digital business card, 750 lira.",
    "Customers who haven't been in touch for 30 days: give them 15% off KEDD Büyüme this weekend and send "
    "them a WhatsApp message about it.",
    "Any unanswered messages?",
    "Create a task: call Aras Bulut tomorrow at 11:00 about the setup.",
    "Make an Instagram post announcing Cirak Agent, our new WhatsApp assistant for small businesses.",
    "Set every price to 1 lira.",
]
MOCK_FILLS = [
    "Add a new silk scarf collection, 850 lira each, 15% off this weekend, tell customers who haven't visited",
    "I have free slots tomorrow afternoon, 20% off hair coloring, tell loyal customers. "
    "Also add keratin treatment, 90 minutes, 2500 lira.",
]


@pytest.fixture
def cfg():
    saved = {}

    def _set(name, value):
        saved.setdefault(name, getattr(settings, name))
        object.__setattr__(settings, name, value)
    yield _set
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def _kedd_set(html: str) -> str:
    return html.split('id="quickKedd"')[1].split("</div>")[0]


def _mock_set(html: str) -> str:
    return html.split('id="quickMock"')[1].split("</div>")[0]


def test_mock_chips_unchanged_and_visible_by_default(cfg, tmp_path):
    cfg("out_dir", tmp_path)
    cfg("public_demo", False)
    html = TestClient(webchan.create_app()).get("/").text
    assert '<div class="quick" id="quickMock">' in html
    assert '<div class="quick" id="quickKedd" hidden>' in html
    for fill in MOCK_FILLS:
        assert f'data-fill="{fill}"' in _mock_set(html)
    assert "Morning brief" in _mock_set(html)


def test_live_business_gets_kedd_chips(cfg, tmp_path, monkeypatch):
    cfg("out_dir", tmp_path)
    cfg("public_demo", True)
    cfg("kedd_api_url", "https://kedd.example")
    cfg("kedd_demo_secret", "demo-secret")
    cfg("kedd_demo_phone", "+900000000000")
    monkeypatch.setattr(KeddBackend, "store_profile", lambda self: {"business_name": "KEDD"})
    html = TestClient(webchan.create_app(), base_url="https://testserver").get("/").text
    assert '<div class="quick" id="quickKedd">' in html
    assert '<div class="quick" id="quickMock" hidden>' in html
    kedd = _kedd_set(html)
    assert "data-tour-jump" in kedd and "Follow the tour" in kedd
    assert 'id="tour"' in html and 'id="tourSteps"' in html


def test_tour_steps_live_in_app_js_in_english():
    src = APP_JS.read_text(encoding="utf-8")
    for text in TOUR_TEXTS:
        assert text in src, text
    assert "paintQuickSet" in src and "function paintTour" in src


def test_system_prompt_no_longer_hard_codes_boutique_salon():
    prompt = agent.SYSTEM_PROMPT
    assert "software company serving small businesses" in prompt
    assert "never assume" in prompt and "a boutique or a salon" in prompt
    for vertical in ("keratin", "fular", "FULAR15", "Saç Kesimi"):
        assert vertical not in prompt and vertical not in prompt.lower()
    assert "Demo Görüşmesi" in prompt and "OZEL20" in prompt
    # task 23 rules and template mechanics untouched
    assert "Messages: Turkish" in prompt and "{{1}}" in prompt
    assert "list_campaign_templates" in prompt
    assert "never treat 'olur' as an approval by itself" in prompt


@pytest.mark.parametrize("scenario", ["live", "fallback"])
def test_kedd_chip_set_follows_live_state(scenario):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    r = subprocess.run(["node", str(HARNESS), str(APP_JS), scenario],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr or r.stdout
    assert f"PASS [{scenario}]" in r.stdout
