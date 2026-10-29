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

KEDD_FILLS = [
    "KEDD Pro'ya bu hafta sonu %20 indirim yap, bir süredir gelmeyen müşterilere haber ver",
    "Yarın 14:00'e Nar Kuaför ile demo görüşmesi ayarla",
    "Yeni WhatsApp asistanımız için Instagram gönderisi hazırla",
    "Ada Kafe'ye 100 tavsiye puanı ekle",
]
KEDD_EN_FILLS = [
    "Give 20% off KEDD Pro this weekend, tell customers who haven't visited in a while",
    "Schedule a demo call with Nar Kuaför tomorrow at 14:00",
    "Prepare an Instagram post for our new WhatsApp assistant",
    "Add 100 referral points to Ada Kafe",
]
MOCK_FILLS = [
    "Yeni ipek fular koleksiyonu ekle, tanesi 850 lira, hafta sonu %15 indirim, "
    "uzun süredir gelmeyenlere haber ver",
    "Yarın öğleden sonra boşluklarım var, saç boyamada %20 indirim, sadık müşterilere haber ver. "
    "Bir de keratin bakımı ekle, 90 dakika, 2500 lira.",
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
    assert "Butik demosu" in _mock_set(html) and "Salon demosu" in _mock_set(html)
    assert 'data-send="/kedd"' in _mock_set(html)


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
    for fill in KEDD_FILLS:
        assert f'data-fill="{fill}"' in kedd
    assert "data-brief=" in kedd and "Sabah özeti" in kedd
    assert "preview only on this page" in kedd
    assert "/kedd" not in kedd  # already on the live business: no switch chip


def test_kedd_chip_english_pairs_in_app_js():
    src = APP_JS.read_text(encoding="utf-8")
    for tr, en in zip(KEDD_FILLS, KEDD_EN_FILLS):
        assert tr in src and en in src
    assert "paintQuickSet" in src


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
