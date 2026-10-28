"""Demo runner: offline, with the model boundary monkeypatched (no network)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from app import agent, meta_client as meta, report


def _load_demo():
    path = Path(__file__).resolve().parent.parent / "scripts" / "demo.py"
    spec = importlib.util.spec_from_file_location("demo_runner", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["demo_runner"] = mod   # dataclasses resolve types via sys.modules
    spec.loader.exec_module(mod)
    return mod


demo = _load_demo()
USAGE = {"input_tokens": 100, "output_tokens": 20}


def _patch(monkeypatch, fail_on=""):
    def fake_plan(ctx, backend, max_revisions=3):
        if fail_on and fail_on in ctx.instruction:
            raise RuntimeError("simulated plan failure")
        if "Bugün ne oldu" in ctx.instruction:
            return SimpleNamespace(proposal=None, answer="Bugün 3 randevun var, ciro 2.450 TL.",
                                   tool_log=[{"tool": "get_daily_summary", "args": {},
                                              "result": {"ok": True}}],
                                   usage=dict(USAGE), turns=1)
        seg = backend.find_customers(inactive_days=60)
        proposal = {"summary_tr": "Test planı: 1 ürün, 1 görsel, 1 kampanya.", "actions": [
            {"type": "add_catalog_item", "item_type": "product", "ref": "n1",
             "name": "Demo Ürün", "price": 100},
            {"type": "generate_visual", "visual_prompt": "demo product", "item_refs": ["n1"]},
            {"type": "send_campaign", "segment_id": seg["segment_id"],
             "message": "Demo kampanya!", "attach_visual": True},
        ]}
        return SimpleNamespace(proposal=proposal, answer=None,
                               tool_log=[{"tool": "find_customers", "args": {}, "result": seg}],
                               usage=dict(USAGE), turns=1)

    def fake_visual(imgs, prompt, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"fake-png")
        return str(dest)

    monkeypatch.setattr(agent, "plan", fake_plan)
    monkeypatch.setattr(report, "summarize_for_merchant", lambda rep: "Demo özeti.")
    monkeypatch.setattr(meta, "generate_campaign_visual", fake_visual)


def test_demo_all_scenarios_offline(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch)
    out = tmp_path / "demo"
    assert demo.main(["--out", str(out), "--no-images"]) == 0
    folders = ["01-butik", "02-service", "03-salon-campaign", "04-appointment", "05-summary"]
    for f in folders:
        d = out / f
        assert (d / "instruction.txt").exists() and (d / "tool_log.json").exists()
        assert (d / "report.json").exists() and (d / "summary.txt").exists()
        assert not (d / "error.txt").exists()
    for f in folders[:4]:
        assert (out / f / "approval_card.txt").exists() and not (out / f / "answer.txt").exists()
    assert (out / "05-summary" / "answer.txt").read_text() == "Bugün 3 randevun var, ciro 2.450 TL."
    assert not (out / "05-summary" / "approval_card.txt").exists()
    assert (out / "01-butik" / "campaign_visual.png").read_bytes() == b"fake-png"
    assert "Demo özeti." in (out / "01-butik" / "summary.txt").read_text()
    assert json.loads((out / "01-butik" / "report.json").read_text())["steps"]
    printed = capsys.readouterr().out
    assert "05-summary" in printed and "TOTAL" in printed


def test_demo_only_runs_one_scenario(tmp_path, monkeypatch):
    _patch(monkeypatch)
    out = tmp_path / "demo"
    assert demo.main(["--out", str(out), "--only", "summary", "--no-images"]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["05-summary"]


def test_demo_failure_does_not_stop_others(tmp_path, monkeypatch):
    _patch(monkeypatch, fail_on="Keratin")
    out = tmp_path / "demo"
    assert demo.main(["--out", str(out), "--no-images"]) == 1
    assert (out / "02-service" / "error.txt").exists()
    assert not (out / "02-service" / "summary.txt").exists()
    for f in ("01-butik", "03-salon-campaign", "04-appointment", "05-summary"):
        assert (out / f / "summary.txt").exists()
