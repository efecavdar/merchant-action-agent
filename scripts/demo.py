"""Demo runner: every README scenario against the mock backend, one tidy folder each.

    python scripts/demo.py [--only butik|salon|summary|appointment|service] [--out out/demo] [--no-images]

Each scenario gets a fresh MockBackend and writes instruction.txt, tool_log.json,
approval_card.txt (or answer.txt when there is nothing to approve), report.json,
summary.txt, and campaign_visual.png when one is produced. A failing scenario is
reported and skipped; the exit code is 1 at the end if any scenario failed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agent, approval, executor, pipeline, report  # noqa: E402
from app.tools.backend import MockBackend  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BAGS_JPG = "demo/sample/bags.jpg"


@dataclass
class Scenario:
    key: str            # --only name
    folder: str         # subfolder under --out
    business: str       # mock backend seed
    instruction: str    # what the merchant said


SCENARIOS = (
    Scenario("butik", "01-butik", "butik",
             "Yeni ipek fular koleksiyonu ekle, tanesi 850 lira, hafta sonu %15 indirim, "
             "uzun süredir gelmeyenlere haber ver"),
    Scenario("service", "02-service", "salon", "Keratin bakımı XL ekle, 90 dakika, 2500 lira"),
    Scenario("salon", "03-salon-campaign", "salon",
             "Yarın öğleden sonra boşluklarım var, saç boyamada %20 indirim, "
             "sadık müşterilere haber ver"),
    Scenario("appointment", "04-appointment", "salon", "Ayşe E.'nin yarınki randevusunu 17:15'e al"),
    Scenario("summary", "05-summary", "salon", "Bugün ne oldu?"),
)


def _select_images(scen: Scenario, no_images: bool) -> list[str]:
    """Input photos for a scenario. Only the boutique one takes a photo today."""
    if scen.key != "butik" or no_images:
        return []
    photo = ROOT / BAGS_JPG
    if photo.exists():
        return [str(photo)]
    print(f"[{scen.folder}] note: no {BAGS_JPG}, running text-only")
    return []


def run_scenario(scen: Scenario, out_dir: Path, no_images: bool) -> dict[str, int]:
    """Run one scenario into out_dir/scen.folder. Returns the run's token usage."""
    scen_dir = out_dir / scen.folder
    scen_dir.mkdir(parents=True, exist_ok=True)
    images = _select_images(scen, no_images)
    backend = MockBackend(scen.business, today=date.today())
    text = scen.instruction + "".join(f"\n[attached: {Path(p).name}]" for p in images) + "\n"
    (scen_dir / "instruction.txt").write_text(text)

    ctx = pipeline.understand_media(scen.instruction, None, images, out_dir=scen_dir)
    run = agent.plan(ctx, backend)
    (scen_dir / "tool_log.json").write_text(json.dumps(run.tool_log, ensure_ascii=False, indent=2,
                                                       default=str))
    if run.proposal is not None:
        seg_counts = {e["result"].get("segment_id"): e["result"].get("count") for e in run.tool_log
                      if e.get("tool") == "find_customers" and isinstance(e.get("result"), dict)}
        card = approval.render_card(run.proposal, ctx, seg_counts)
        (scen_dir / "approval_card.txt").write_text(card)
        steps = executor.execute(run.proposal, ctx, backend, out_dir=scen_dir)
        visual = next((s["result"]["visual_path"] for s in steps
                       if s["action"] == "generate_visual" and s["status"] == "ok"), "")
        if visual and Path(visual).exists():
            dst = scen_dir / "campaign_visual.png"
            if Path(visual).resolve() != dst.resolve():
                shutil.copy(visual, dst)
    elif run.answer:
        (scen_dir / "answer.txt").write_text(run.answer)
        print(f"[{scen.folder}] answer:\n{run.answer}")
        steps = []
    else:
        raise RuntimeError("agent returned neither a proposal nor an answer")

    rep = report.build_report(steps, backend)
    (scen_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    summary = run.answer if run.proposal is None else report.summarize_for_merchant(rep)
    (scen_dir / "summary.txt").write_text(summary)
    return dict(run.usage)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run every demo scenario into out/demo/, one folder each.")
    ap.add_argument("--only", choices=[s.key for s in SCENARIOS], help="run a single scenario")
    ap.add_argument("--out", default="out/demo", help="output folder (default: out/demo)")
    ap.add_argument("--no-images", action="store_true", help="skip input photos even if present")
    a = ap.parse_args(argv)
    out = Path(a.out)
    selected = [s for s in SCENARIOS if not a.only or s.key == a.only]

    results: list[dict[str, Any]] = []
    for scen in selected:
        t0 = time.perf_counter()
        try:
            usage = run_scenario(scen, out, a.no_images)
            status = "ok"
        except Exception as e:  # noqa: BLE001 — one bad scenario must not stop the reel
            usage, status = {"input_tokens": 0, "output_tokens": 0}, "FAILED"
            (out / scen.folder).mkdir(parents=True, exist_ok=True)
            (out / scen.folder / "error.txt").write_text(traceback.format_exc())
            print(f"[{scen.folder}] FAILED: {e}")
        seconds = time.perf_counter() - t0
        results.append({"folder": scen.folder, "status": status, "seconds": seconds,
                        "in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0)})
        if status == "ok":
            print(f"[{scen.folder}] ok in {seconds:.1f}s · "
                  f"{usage.get('input_tokens', 0):,} in / {usage.get('output_tokens', 0):,} out tok")

    print(f"\n{'scenario':<18}{'status':<8}{'seconds':>8}  {'in-tok':>8}  {'out-tok':>8}")
    for r in results:
        print(f"{r['folder']:<18}{r['status']:<8}{r['seconds']:>8.1f}  {r['in']:>8,}  {r['out']:>8,}")
    if len(results) > 1:
        tot_s = sum(r["seconds"] for r in results)
        tot_in, tot_out = sum(r["in"] for r in results), sum(r["out"] for r in results)
        print(f"{'TOTAL':<18}{'':<8}{tot_s:>8.1f}  {tot_in:>8,}  {tot_out:>8,}")
    return 1 if any(r["status"] != "ok" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
