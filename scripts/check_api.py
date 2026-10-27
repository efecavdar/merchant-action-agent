"""Smoke test for the Meta Model API setup. Run this first.

    python scripts/check_api.py                       # models + Spark hello (Turkish)
    python scripts/check_api.py --audio note.ogg      # + Muse Voice Transcribe
    python scripts/check_api.py --image bags.jpg --concept handbag   # + SAM 3.1
    python scripts/check_api.py --image-gen            # + Muse Image (1 image, $0.01)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import meta_client as meta  # noqa: E402
from app.config import settings  # noqa: E402


def step(title: str):
    print(f"\n=== {title} ===")
    return time.time()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio")
    ap.add_argument("--image")
    ap.add_argument("--concept", default="product")
    ap.add_argument("--image-gen", action="store_true")
    a = ap.parse_args()

    t = step("1. list models")
    models = sorted(m.id for m in meta.client().models.list().data)
    print(", ".join(models))
    for need in (settings.orchestrator_model, settings.image_model, settings.sam_model):
        print(f"  {'✓' if need in models else '✗ MISSING'} {need}")
    print(f"  ({time.time()-t:.1f}s)")

    t = step("2. Muse Spark — Turkish hello")
    print(meta.chat("Bursa'daki bir butik sahibine tek cümlelik samimi bir hoş geldin mesajı yaz.", effort="minimal"))
    print(f"  ({time.time()-t:.1f}s)")

    if a.audio:
        t = step("3. Muse Voice Transcribe")
        res = meta.transcribe(a.audio, language_bias=("Turkish", "English"))
        print(res["transcript"])
        print(f"  duration {res.get('audioDurationMs',0)/1000:.1f}s  ({time.time()-t:.1f}s)")

    if a.image:
        t = step(f"4. SAM 3.1 — '{a.concept}'")
        img = meta.load_image(a.image)
        boxes = meta.segment(img, a.concept)
        for b in boxes:
            print(f"  id={b.object_id} box=({b.x1},{b.y1})-({b.x2},{b.y2}) conf={b.confidence}")
        crops = meta.crop_boxes(img, boxes)
        out = Path("out/smoke"); out.mkdir(parents=True, exist_ok=True)
        for i, (_, c) in enumerate(crops):
            c.save(out / f"crop_{i}.png")
        print(f"  {len(crops)} crop(s) saved to {out}/  ({time.time()-t:.1f}s)")

        t = step("5. Muse Spark vision — describe first crop")
        if crops:
            print(meta.describe_image(crops[0][1], "Bu ürünü Türkçe tek cümleyle tanımla."))
        print(f"  ({time.time()-t:.1f}s)")

    if a.image_gen:
        t = step("6. Muse Image — one test image")
        p = meta.generate_campaign_visual([], "minimal boutique flat lay, beige tones, square", "out/smoke/test_visual.png")
        print(f"  saved {p}  ({time.time()-t:.1f}s)")

    print("\nAll requested checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
