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
from openai import BadRequestError, OpenAI
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

MAX_VOICE_S = 3 * 60  # longer voice notes are rejected before transcription
_TOO_LONG_TR = "Sesli not 3 dakikadan uzun, kısaltıp tekrar dener misin?"


def _audio_duration_s(path: Path) -> float | None:
    """Media duration in seconds via ffprobe, or None when ffprobe is unavailable."""
    if shutil.which("ffprobe") is None:
        return None
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return None


def _wav_duration_s(path: Path) -> float:
    import wave  # stdlib: no ffprobe needed to measure the WAV we just wrote
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def to_wav_24k_mono(src: str | Path) -> Path:
    """Convert any audio (ogg/opus from WhatsApp, m4a, mp3, webm...) to the WAV the endpoint
    accepts: RIFF/WAVE, mono, 16-bit PCM, 24 kHz. Requires ffmpeg on PATH. Rejects
    recordings longer than 3 minutes and files ffmpeg cannot decode."""
    src = Path(src)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH; install it or pass a 24 kHz mono 16-bit WAV.")
    duration = _audio_duration_s(src)
    if duration is not None and duration > MAX_VOICE_S:
        raise ValueError(_TOO_LONG_TR)
    out = Path(tempfile.mkdtemp()) / (src.stem + ".wav")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", "-map_metadata", "-1", str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        raise ValueError("Sesli not açılamadı, başka bir kayıt dener misin?") from None
    if duration is None and _wav_duration_s(out) > MAX_VOICE_S:
        out.unlink(missing_ok=True)
        raise ValueError(_TOO_LONG_TR)
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
    if not (data.get("transcript") or "").strip():
        raise ValueError("Sesli notu anlayamadım, tekrar dener misin ya da yazar mısın?")
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


def _segment_request(payload: list[dict[str, Any]], extra: dict[str, Any]) -> list[Box]:
    text = ""
    with client().responses.stream(model=settings.sam_model, input=payload, **extra) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                text += event.delta
    return parse_sam_boxes(text)


def segment(img: Image.Image, concept: str, include_confidence: bool = True) -> list[Box]:
    """Find every instance of `concept` (a short noun phrase, e.g. "handbag") in the image.

    The `metadata` extras were written from the docs and may not exist server-side: a 400
    is retried once with no extras at all. Anything else propagates to the pipeline, which
    falls back to the whole image."""
    payload = [{
        "type": "message",
        "role": "user",
        "content": [
            {"type": "input_text", "text": concept},
            {"type": "input_image", "image_url": image_to_data_url(img)},
        ],
    }]
    extra: dict[str, Any] = {"metadata": {"mask_encoding": "one_bit"}}
    if include_confidence:
        extra["metadata"]["include_confidence"] = "true"
    try:
        boxes = _segment_request(payload, extra)
    except BadRequestError as e:
        if e.status_code != 400:
            raise
        log.warning("SAM rejected the metadata params (400); retrying without them")
        boxes = _segment_request(payload, {})
    log.info("SAM found %d '%s'", len(boxes), concept)
    return boxes


MAX_CROPS_PER_PHOTO = 8


def _iou(a: Box, b: Box) -> float:
    inter = max(0, min(a.x2, b.x2) - max(a.x1, b.x1)) * max(0, min(a.y2, b.y2) - max(a.y1, b.y1))
    union = a.area + b.area - inter
    return inter / union if union else 0.0


def _deduplicate(boxes: list[Box], iou_threshold: float = 0.6) -> list[Box]:
    """Drop near-duplicate detections, keeping the most confident of each cluster."""
    kept: list[Box] = []
    for b in sorted(boxes, key=lambda b: (-(b.confidence or 0.0), -b.area, b.x1)):
        if all(_iou(b, k) <= iou_threshold for k in kept):
            kept.append(b)
    return kept


def crop_boxes(img: Image.Image, boxes: list[Box], pad: float = 0.04,
               min_area_ratio: float = 0.01) -> list[tuple[Box, Image.Image]]:
    """Crop each box (with a small margin). Drops tiny detections, near-full-frame boxes when
    several detections exist (background false positives), and IoU > 0.6 duplicates; caps at
    MAX_CROPS_PER_PHOTO crops. Sorted left-to-right."""
    w, h = img.size
    area = w * h
    survivors = [b for b in boxes if b.area / area >= min_area_ratio]
    if len(survivors) > 1:
        survivors = [b for b in survivors if b.area / area <= 0.9]
    survivors = _deduplicate(survivors)
    if len(survivors) > MAX_CROPS_PER_PHOTO:
        log.info("capping %d detections to %d crops", len(survivors), MAX_CROPS_PER_PHOTO)
    out = []
    for b in sorted(survivors, key=lambda b: b.x1)[:MAX_CROPS_PER_PHOTO]:
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

def _images_call(call, **kwargs):
    """Call images.edit/generate, retrying once with only `size` when the API rejects the
    extra fields with a 400 (they were written from the docs, untested against the live API)."""
    try:
        return call(**kwargs)
    except BadRequestError as e:
        if e.status_code != 400:
            raise
        log.warning("Muse Image rejected the extra fields (400); retrying with size only")
        kwargs["extra_body"] = {"size": kwargs["extra_body"]["size"]}
        return call(**kwargs)


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
        resp = _images_call(
            client().images.edit,
            model=settings.image_model, prompt=prompt, image=files, n=1,
            extra_body={"size": size, "output_format": "png",
                        "tool_enablement": {"enable_web_search": False, "enable_shell": False}},
        )
    else:
        resp = _images_call(
            client().images.generate,
            model=settings.image_model, prompt=prompt, n=1,
            extra_body={"size": size, "output_format": "png"},
        )
    b64 = resp.data[0].b64_json
    out_path.write_bytes(base64.b64decode(b64))
    log.info("campaign visual saved to %s", out_path)
    return out_path
