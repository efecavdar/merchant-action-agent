"""Media understanding pipeline: turns a voice note + product photos into structured context.

    voice note ──► Muse Voice Transcribe ──► instruction text
    photos ─────► SAM 3.1 (segment) ──► crops ──► Muse Spark (vision) ──► product cards

The output (`MediaContext`) is what the orchestrator reasons over.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from . import meta_client as meta
from .config import settings

log = logging.getLogger(__name__)


@dataclass
class ProductCard:
    temp_id: str                 # "tmp1"... resolved to a real product id at execution time
    crop_path: str
    source_image: str
    name: str
    category: str
    color: str
    material: str
    description: str
    sam_confidence: float | None = None


@dataclass
class MediaContext:
    instruction: str                     # transcript or typed text
    instruction_source: str              # "voice" | "text"
    products: list[ProductCard] = field(default_factory=list)
    concept: str = ""                    # what SAM was asked to find
    notes: list[str] = field(default_factory=list)

    def to_prompt_json(self) -> dict[str, Any]:
        return {
            "instruction": self.instruction,
            "instruction_source": self.instruction_source,
            "detected_products": [
                {"temp_id": p.temp_id, "name": p.name, "category": p.category,
                 "color": p.color, "material": p.material, "description": p.description}
                for p in self.products
            ],
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #

def infer_concept(instruction: str) -> str:
    """Ask Muse Spark which single object type the merchant is talking about, as an
    English noun phrase SAM understands ("handbag", "sneaker", "dress")."""
    reply = meta.chat(
        f'Merchant instruction (Turkish or English): "{instruction}"\n'
        "Which ONE physical product type is being added to the catalog? Answer with a short "
        "English noun phrase suitable for an object detector, e.g. 'handbag', 'leather belt', "
        "'dress'. If unclear, answer 'product'. Reply with the noun phrase only.",
        effort="minimal",
    )
    concept = reply.strip().strip('"').strip(".").lower() or "product"
    return concept[:40]


_DESCRIBE_PROMPT = (
    "You are cataloguing products for a small Turkish boutique. Look at the image and return ONLY a JSON "
    "object with keys: name (short Turkish product name, max 5 words), category (one of: çanta, ayakkabı, "
    "aksesuar, elbise, üst giyim, alt giyim, diğer), color (Turkish), material (Turkish, or 'belirsiz'), "
    "description (one friendly Turkish sentence for a WhatsApp catalog, no price). No markdown."
)


def describe_crop(crop: Image.Image) -> dict[str, str]:
    raw = meta.describe_image(crop, _DESCRIBE_PROMPT)
    try:
        data = meta.extract_json(raw)
    except ValueError:
        log.warning("vision reply was not JSON, using fallback: %s", raw[:120])
        data = {}
    return {
        "name": str(data.get("name") or "Yeni Ürün"),
        "category": str(data.get("category") or "diğer"),
        "color": str(data.get("color") or ""),
        "material": str(data.get("material") or "belirsiz"),
        "description": str(data.get("description") or ""),
    }


def understand_media(text: str | None, audio: str | None, images: list[str],
                     concept: str | None = None, out_dir: Path | None = None) -> MediaContext:
    """Run the full media pipeline. Either `text` or `audio` must be given."""
    out_dir = out_dir or settings.out_dir
    crops_dir = out_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    # 1) instruction
    if audio:
        asr = meta.transcribe(audio, language_bias=("Turkish", "English"))
        ctx = MediaContext(instruction=asr["transcript"].strip(), instruction_source="voice")
        ctx.notes.append(f"voice note transcribed ({asr.get('audioDurationMs', 0)/1000:.1f}s)")
    elif text:
        ctx = MediaContext(instruction=text.strip(), instruction_source="text")
    else:
        raise ValueError("need text or audio")

    if not images:
        return ctx

    # 2) what to look for
    ctx.concept = concept or infer_concept(ctx.instruction)
    log.info("segmenting for concept: %s", ctx.concept)

    # 3) segment + describe
    n = 0
    for img_path in images:
        img = meta.load_image(img_path)
        boxes = meta.segment(img, ctx.concept)
        pairs = meta.crop_boxes(img, boxes)
        if not pairs:
            ctx.notes.append(f"SAM found no '{ctx.concept}' in {Path(img_path).name}; using whole image")
            pairs = [(None, img)]
        for box, crop in pairs:
            n += 1
            crop_path = crops_dir / f"tmp{n}.png"
            crop.save(crop_path)
            info = describe_crop(crop)
            ctx.products.append(ProductCard(
                temp_id=f"tmp{n}", crop_path=str(crop_path), source_image=str(img_path),
                sam_confidence=(box.confidence if box else None), **info,
            ))
    ctx.notes.append(f"{n} product(s) detected across {len(images)} photo(s)")
    return ctx
