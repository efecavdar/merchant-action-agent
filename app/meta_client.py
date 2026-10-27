"""Thin wrappers around Meta Model API.

Four Meta models are used, each through its documented endpoint:

* Muse Spark 1.3  — reasoning / orchestration / vision   (Responses API, tool calling)
* Muse Voice Transcribe 1.0 — speech to text             (POST /v1/asr/transcribe)
* SAM 3.1          — product segmentation                 (Responses API, streamed special-token output)
* Muse Image 1.0   — campaign visual generation           (POST /v1/images/edits, /v1/images/generations)

Docs: https://dev.meta.ai/docs/overview
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests
from openai import OpenAI
from PIL import Image

from .config import require_api_key, settings

log = logging.getLogger(__name__)

_client: OpenAI | None = None


def client() -> OpenAI:
    """OpenAI SDK pointed at Meta Model API (drop-in compatible)."""
    global _client
    if _client is None:
        _client = OpenAI(base_url=settings.base_url, api_key=require_api_key())
    return _client


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {require_api_key()}"}


# --------------------------------------------------------------------------- #
# Images helpers
# --------------------------------------------------------------------------- #

MAX_SIDE = 1536  # keep uploads small; SAM boxes are returned in the pixels we send


def load_image(path: str | Path, max_side: int = MAX_SIDE) -> Image.Image:
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def image_to_data_url(img: Image.Image, fmt: str = "JPEG", quality: int = 90) -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    mime = "image/jpeg" if fmt.upper() == "JPEG" else f"image/{fmt.lower()}"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


# --------------------------------------------------------------------------- #
# Muse Voice Transcribe — speech to text
# --------------------------------------------------------------------------- #

def to_wav_24k_mono(src: str | Path) -> Path:
    """Convert any audio (ogg/opus from WhatsApp, m4a, mp3...) to the WAV the endpoint accepts:
    RIFF/WAVE, mono, 16-bit PCM, 24 kHz. Requires ffmpeg on PATH."""
    src = Path(src)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH; install it or pass a 24 kHz mono 16-bit WAV.")
    out = Path(tempfile.mkdtemp()) / (src.stem + ".wav")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", "-map_metadata", "-1", str(out)]
    subprocess.run(cmd, check=True)
    return out


def transcribe(audio_path: str | Path, language_bias: Iterable[str] = ("Turkish",),
               keywords: Iterable[str] = ()) -> dict[str, Any]:
    """Transcribe a recording with Muse Voice Transcribe. Returns the JSON response
    ({"transcript": ..., "turns": [...], "audioDurationMs": ...})."""
    wav = to_wav_24k_mono(audio_path)
    request = {
        "mode": "PUSH_TO_TALK",          # single utterance: a voice note
        "model": settings.asr_model,
        "audioEncoding": "WAV",
        "languageBias": list(language_bias),
    }
    kw = list(keywords)
    if kw:
        request["keywords"] = kw
    with open(wav, "rb") as f:
        resp = requests.post(
            f"{settings.base_url}/asr/transcribe",
            headers=_auth_headers(),
            files={
                "request": (None, json.dumps(request), "application/json"),
                "audio": (wav.name, f, "audio/wav"),
            },
            timeout=120,
        )
    resp.raise_for_status()
    data = resp.json()
    log.info("transcribed %.1fs of audio", data.get("audioDurationMs", 0) / 1000)
    return data


# --------------------------------------------------------------------------- #
# SAM 3.1 — segmentation
# --------------------------------------------------------------------------- #

@dataclass
class Box:
    object_id: int
    x1: int
    y1: int
    x2: int
    y2: int
    frame_w: int
    frame_h: int
    confidence: float | None = None

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)


_BOX_RE = re.compile(
    r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>"
)


def parse_sam_boxes(output_text: str) -> list[Box]:
    """Parse the SAM wire grammar (one line per frame) into boxes.

    Grammar: <Nf>id<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..[;c=..]|><|mask;...|>,id<|box;...|>...
    We only need boxes to crop products; masks are available via `meta-sam-parser` if needed.
    """
    boxes: list[Box] = []
    for m in _BOX_RE.finditer(output_text):
        oid, x1, y1, x2, y2, w, h, c = m.groups()
        boxes.append(Box(int(oid), int(x1), int(y1), int(x2), int(y2), int(w), int(h),
                         float(c) if c else None))
    return boxes


def segment(img: Image.Image, concept: str, include_confidence: bool = True) -> list[Box]:
    """Find every instance of `concept` (a short noun phrase, e.g. "handbag") in the image."""
    extra: dict[str, Any] = {"metadata": {"mask_encoding": "one_bit"}}
    if include_confidence:
        extra["metadata"]["include_confidence"] = "true"
    text = ""
    with client().responses.stream(
        model=settings.sam_model,
        input=[{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": concept},
                {"type": "input_image", "image_url": image_to_data_url(img)},
            ],
        }],
        **extra,
    ) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                text += event.delta
    boxes = parse_sam_boxes(text)
    log.info("SAM found %d '%s'", len(boxes), concept)
    return boxes


def crop_boxes(img: Image.Image, boxes: list[Box], pad: float = 0.04,
               min_area_ratio: float = 0.01) -> list[tuple[Box, Image.Image]]:
    """Crop each box (with a small margin), dropping tiny detections. Sorted left-to-right."""
    w, h = img.size
    out = []
    for b in sorted(boxes, key=lambda b: b.x1):
        if b.area / (w * h) < min_area_ratio:
            continue
        px, py = int((b.x2 - b.x1) * pad), int((b.y2 - b.y1) * pad)
        crop = img.crop((max(0, b.x1 - px), max(0, b.y1 - py), min(w, b.x2 + px), min(h, b.y2 + py)))
        out.append((b, crop))
    return out


# --------------------------------------------------------------------------- #
# Muse Spark — vision + text (Responses API)
# --------------------------------------------------------------------------- #

_JSON_RE = re.compile(r"\{.*\}", re.S)


def extract_json(text: str) -> Any:
    """Pull the first JSON object out of a model reply (tolerates ``` fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError(f"no JSON object in reply: {text[:200]}")
    return json.loads(m.group(0))


def describe_image(img: Image.Image, instruction: str, model: str | None = None) -> str:
    """Ask Muse Spark about an image; returns raw text."""
    resp = client().responses.create(
        model=model or settings.vision_model,
        reasoning={"effort": "low"},
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": instruction},
                {"type": "input_image", "image_url": image_to_data_url(img)},
            ],
        }],
    )
    return resp.output_text


def chat(instruction: str, system: str | None = None, model: str | None = None,
         effort: str = "low") -> str:
    """Plain text completion on Muse Spark."""
    kwargs: dict[str, Any] = {}
    if system:
        kwargs["instructions"] = system
    resp = client().responses.create(
        model=model or settings.orchestrator_model,
        reasoning={"effort": effort},
        input=instruction,
        **kwargs,
    )
    return resp.output_text


# --------------------------------------------------------------------------- #
# Muse Image — campaign visuals
# --------------------------------------------------------------------------- #

def generate_campaign_visual(product_crops: list[Image.Image], prompt: str,
                             out_path: str | Path, size: str = "1024x1024") -> Path:
    """Compose the (real) product crops into one promotional visual with Muse Image.

    Uses /v1/images/edits with multiple reference images so the products in the visual
    are the merchant's actual products, not hallucinated ones.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for i, crop in enumerate(product_crops[:6]):
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        buf.seek(0)
        buf.name = f"product_{i}.png"
        files.append(buf)
    if files:
        resp = client().images.edit(
            model=settings.image_model, prompt=prompt, image=files, n=1,
            extra_body={"size": size, "output_format": "png",
                        "tool_enablement": {"enable_web_search": False, "enable_shell": False}},
        )
    else:
        resp = client().images.generate(
            model=settings.image_model, prompt=prompt, n=1,
            extra_body={"size": size, "output_format": "png"},
        )
    b64 = resp.data[0].b64_json
    out_path.write_bytes(base64.b64decode(b64))
    log.info("campaign visual saved to %s", out_path)
    return out_path
