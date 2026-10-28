"""Command-line entry point.

    python -m app.cli run --text "..." --images demo/sample/bags.jpg
    python -m app.cli run --audio demo/sample/voice.ogg --images a.jpg b.jpg --auto-approve
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import settings
from . import agent, approval, executor, pipeline, report
from .tools.backend import get_backend


def cmd_run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = get_backend(args.business)
    print(f"[backend] {backend.name}" + (f" ({args.business})" if args.business else ""))

    # 1) understand media (Voice Transcribe + SAM 3.1 + Spark vision)
    ctx = pipeline.understand_media(args.text, args.audio, args.images, concept=args.concept, out_dir=out_dir)
    print(f"[instruction] ({ctx.instruction_source}) {ctx.instruction}")
    for p in ctx.products:
        print(f"[detected] {p.temp_id}: {p.name} / {p.category} / {p.color} / {p.material}")
    for n in ctx.notes:
        print(f"[note] {n}")

    # 2) plan (Spark + tools) → validated proposal
    run = agent.plan(ctx, backend)
    (out_dir / "tool_log.json").write_text(json.dumps(run.tool_log, ensure_ascii=False, indent=2, default=str))
    print(f"[agent] {run.turns} turn(s), tokens in/out: {run.usage['input_tokens']}/{run.usage['output_tokens']}")
    if run.answer:
        print("\n" + "─" * 60 + "\n" + run.answer + "\n" + "─" * 60 + "\n[done] answered, nothing to execute")
        return 0
    (out_dir / "proposal.json").write_text(json.dumps(run.proposal, ensure_ascii=False, indent=2))

    # 3) human approval
    seg_counts = {}
    for entry in run.tool_log:
        if entry.get("tool") == "find_customers" and isinstance(entry.get("result"), dict):
            seg_counts[entry["result"].get("segment_id")] = entry["result"].get("count")
    card = approval.render_card(run.proposal, ctx, seg_counts, approval.card_facts(run.tool_log))
    (out_dir / "approval_card.txt").write_text(card)
    ok = (approval.auto_approve if args.auto_approve else approval.ask_console)(card)
    if not ok:
        print("[approval] rejected — nothing executed.")
        return 0

    # 4) execute (deterministic) + 5) report
    steps = executor.execute(run.proposal, ctx, backend, out_dir=out_dir)
    rep = report.build_report(steps, backend)
    (out_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    if hasattr(backend, "save"):
        backend.save()
    summary = report.summarize_for_merchant(rep)
    (out_dir / "summary.txt").write_text(summary)
    print("\n" + "─" * 60 + "\n" + summary + "\n" + "─" * 60)
    print(f"[done] artifacts in {out_dir}/")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="merchant-action-agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the full flow")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="typed instruction (Turkish or English)")
    g.add_argument("--audio", help="voice note (ogg/opus/m4a/mp3/wav); needs ffmpeg")
    r.add_argument("--images", nargs="*", default=[], help="product photos")
    r.add_argument("--concept", help="override what SAM should look for, e.g. 'handbag'")
    r.add_argument("--business", choices=["butik", "salon"], help="mock backend seed (default: MOCK_BUSINESS)")
    r.add_argument("--auto-approve", action="store_true", help="skip the console prompt (demo/CI)")
    r.add_argument("--out", default=str(settings.out_dir))
    r.set_defaults(fn=cmd_run)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
