"""Task 15, item 17: queued approval words are held after a card arrives (node harness)."""

import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "fixtures" / "web_queue_harness.js"
APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


@pytest.mark.parametrize("scenario,queued", [
    ("hold", "evet"), ("hold", "onayla"), ("hold", "Evet onaylıyorum!"), ("hold", "fiyat ne kadar"),
    ("pass", "fiyat ne kadar"),
])
def test_t15_queued_approval_word_after_card(scenario, queued):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    r = subprocess.run(["node", str(HARNESS), str(APP_JS), scenario, queued],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr or r.stdout
    assert f"PASS [{scenario}]" in r.stdout
