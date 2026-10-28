"""Media pipeline hardening (task 10): voice + photo failure modes, all offline.

Tests that need real ffmpeg are skipped when it is unavailable; everything else
monkeypatches the client/stream/subprocess boundary.
"""

import base64
import importlib.util
import io
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError
from PIL import Image

from app import meta_client as meta
from app import pipeline
from app.config import settings
from app.meta_client import Box


def _bad_request(status=400):
    return BadRequestError("rejected", response=httpx.Response(
        status, request=httpx.Request("POST", "https://api.meta.ai/v1")), body=None)


def _which(fake):
    """Fake shutil.which from a {name: path-or-None} mapping."""
    return lambda name: fake.get(name)


# --------------------------------------------------------------------------- #
# voice: to_wav_24k_mono
# --------------------------------------------------------------------------- #

def test_to_wav_rejects_long_audio_before_converting(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", _which({"ffmpeg": "/usr/bin/ffmpeg", "ffprobe": "/usr/bin/ffprobe"}))
    monkeypatch.setattr(meta, "_audio_duration_s", lambda p: 200.0)

    def boom(*a, **k):
        raise AssertionError("must reject before running ffmpeg")
    monkeypatch.setattr(subprocess, "run", boom)
    src = tmp_path / "long.ogg"
    src.write_bytes(b"fake")
    with pytest.raises(ValueError, match="Sesli not 3 dakikadan uzun"):
        meta.to_wav_24k_mono(src)


def test_to_wav_duration_boundary(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", _which({"ffmpeg": "/usr/bin/ffmpeg", "ffprobe": "/usr/bin/ffprobe"}))
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: ran.append(cmd))
    src = tmp_path / "n.ogg"
    src.write_bytes(b"fake")
    monkeypatch.setattr(meta, "_audio_duration_s", lambda p: 180.0)
    assert meta.to_wav_24k_mono(src).suffix == ".wav" and len(ran) == 1   # exactly 3:00 passes
    monkeypatch.setattr(meta, "_audio_duration_s", lambda p: 180.1)
    with pytest.raises(ValueError, match="Sesli not 3 dakikadan uzun"):
        meta.to_wav_24k_mono(src)
    assert len(ran) == 1                                                 # no second conversion


def test_to_wav_no_ffmpeg_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", _which({}))
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        meta.to_wav_24k_mono(tmp_path / "n.ogg")


def test_to_wav_corrupt_audio_is_turkish_valueerror(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", _which({"ffmpeg": "/usr/bin/ffmpeg"}))
    monkeypatch.setattr(meta, "_audio_duration_s", lambda p: 5.0)

    def fail(*a, **k):
        raise subprocess.CalledProcessError(1, ["ffmpeg"])
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(ValueError, match="Sesli not açılamadı"):
        meta.to_wav_24k_mono(tmp_path / "broken.ogg")


def _write_wav(path, seconds, rate=24000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * rate * 2 * seconds)


def test_to_wav_measures_wav_when_ffprobe_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", _which({"ffmpeg": "/usr/bin/ffmpeg"}))  # no ffprobe
    assert meta._audio_duration_s(tmp_path) is None

    def fake_run(cmd, **k):
        _write_wav(cmd[-1], fake_run.seconds)
    fake_run.seconds = 10
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert meta.to_wav_24k_mono(tmp_path / "n.ogg").suffix == ".wav"

    fake_run.seconds = 200
    with pytest.raises(ValueError, match="Sesli not 3 dakikadan uzun"):
        meta.to_wav_24k_mono(tmp_path / "long.ogg")


def _ffmpeg_has(encoder):
    if shutil.which("ffmpeg") is None:
        return False
    r = subprocess.run(["ffmpeg", "-hide_banner", "-h", f"encoder={encoder}"],
                       capture_output=True)
    return r.returncode == 0


def _sine(out, encoder, seconds=1):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", encoder, str(out)], check=True)


def _assert_wav_24k_mono(path):
    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert 0.5 < w.getnframes() / w.getframerate() < 2.0


@pytest.mark.parametrize("suffix,encoder", [(".ogg", "libopus"), (".webm", "libopus")])
def test_voice_opus_formats_convert(tmp_path, suffix, encoder):
    if not _ffmpeg_has(encoder):
        pytest.skip(f"ffmpeg without {encoder}")
    src = tmp_path / f"note{suffix}"
    _sine(src, encoder)
    _assert_wav_24k_mono(meta.to_wav_24k_mono(src))


@pytest.mark.parametrize("suffix,encoder", [(".m4a", "aac"), (".mp3", "libmp3lame")])
def test_voice_m4a_mp3_convert(tmp_path, suffix, encoder):
    if not _ffmpeg_has(encoder):
        pytest.skip(f"ffmpeg without {encoder}")
    src = tmp_path / f"note{suffix}"
    _sine(src, encoder)
    _assert_wav_24k_mono(meta.to_wav_24k_mono(src))


# --------------------------------------------------------------------------- #
# voice: transcribe
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("payload", [{"transcript": "  "}, {"transcript": ""}, {}])
def test_transcribe_empty_transcript_is_turkish_valueerror(monkeypatch, tmp_path, payload):
    wav = tmp_path / "n.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 40)
    monkeypatch.setattr(meta, "to_wav_24k_mono", lambda p: wav)
    monkeypatch.setattr(meta.requests, "post", lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: dict(payload)))
    with pytest.raises(ValueError, match="Sesli notu anlayamadım"):
        meta.transcribe(tmp_path / "n.ogg")


def test_transcribe_returns_transcript(monkeypatch, tmp_path):
    wav = tmp_path / "n.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 40)
    monkeypatch.setattr(meta, "to_wav_24k_mono", lambda p: wav)
    body = {"transcript": "Fular ekle", "audioDurationMs": 1500}
    monkeypatch.setattr(meta.requests, "post", lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: body))
    assert meta.transcribe(tmp_path / "n.ogg")["transcript"] == "Fular ekle"


# --------------------------------------------------------------------------- #
# SAM: segment retry
# --------------------------------------------------------------------------- #

_BOX_LINE = "<0f>0<|box;x1=10;y1=10;x2=50;y2=50;w=100;h=100;c=0.9|>"


class _FakeStream:
    """Minimal stand-in for the OpenAI SDK streaming context manager."""

    def __init__(self, text=""):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        yield SimpleNamespace(type="response.output_text.delta", delta=self.text)


def _fake_sam_client(monkeypatch, outcomes):
    """`outcomes`: one _FakeStream or exception per stream() call. Returns the calls."""
    calls = []

    class Responses:
        def stream(self, **kwargs):
            calls.append(kwargs)
            nxt = outcomes[len(calls) - 1]
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

    monkeypatch.setattr(meta, "client", lambda: SimpleNamespace(responses=Responses()))
    return calls


def test_segment_retries_without_extras_on_400(monkeypatch):
    calls = _fake_sam_client(monkeypatch, [_bad_request(400), _FakeStream(_BOX_LINE)])
    boxes = meta.segment(Image.new("RGB", (100, 100)), "handbag")
    assert [(b.x1, b.y1, b.x2, b.y2) for b in boxes] == [(10, 10, 50, 50)]
    assert calls[0]["metadata"] == {"mask_encoding": "one_bit", "include_confidence": "true"}
    assert "metadata" not in calls[1] and len(calls) == 2


@pytest.mark.parametrize("error", [_bad_request(500), RuntimeError("stream died")])
def test_segment_reraises_non_400(monkeypatch, error):
    calls = _fake_sam_client(monkeypatch, [error])
    with pytest.raises(type(error)):
        meta.segment(Image.new("RGB", (100, 100)), "handbag")
    assert len(calls) == 1   # no retry: the pipeline falls back to the whole image


# --------------------------------------------------------------------------- #
# SAM: crop_boxes with synthetic boxes
# --------------------------------------------------------------------------- #

def _box(x1, y1, x2, y2, conf=None, oid=0):
    return Box(oid, x1, y1, x2, y2, 100, 100, conf)


def test_crop_boxes_drops_tiny_and_background():
    img = Image.new("RGB", (100, 100))
    tiny = _box(0, 0, 5, 5)               # 0.25 % of the frame
    huge = _box(2, 2, 97, 97)             # ~90 %: background false positive
    exact90 = _box(0, 0, 90, 100)         # exactly 90 %: kept (> 90 % is dropped)
    normal = _box(10, 50, 40, 80, 0.8)
    pairs = meta.crop_boxes(img, [tiny, huge, exact90, normal])
    assert [b.x1 for b, _ in pairs] == [0, 10]
    assert pairs[1][1].size == (32, 32)   # 30 px box + 4 % pad, rounded to 1 px a side


def test_crop_boxes_keeps_single_big_box():
    img = Image.new("RGB", (100, 100))
    assert len(meta.crop_boxes(img, [_box(2, 2, 97, 97)])) == 1   # one detection filling the frame is legit


def test_crop_boxes_dedup_keeps_most_confident():
    img = Image.new("RGB", (100, 100))
    weak = _box(10, 10, 50, 50, 0.4)     # IoU ≈ 0.90 with `strong`
    strong = _box(12, 12, 50, 50, 0.9)
    pairs = meta.crop_boxes(img, [weak, strong])
    assert len(pairs) == 1 and pairs[0][0].confidence == 0.9


def test_crop_boxes_keeps_low_iou_pair():
    img = Image.new("RGB", (100, 100))
    pairs = meta.crop_boxes(img, [_box(50, 50, 70, 70), _box(10, 10, 30, 30)])
    assert [b.x1 for b, _ in pairs] == [10, 50]   # both kept, left-to-right


def test_crop_boxes_caps_at_eight():
    img = Image.new("RGB", (200, 20))
    boxes = [_box(x, 0, x + 8, 10) for x in range(0, 200, 20)]
    assert len(boxes) == 10
    pairs = meta.crop_boxes(img, boxes)
    assert len(pairs) == 8 and [b.x1 for b, _ in pairs] == sorted(b.x1 for b, _ in pairs)


# --------------------------------------------------------------------------- #
# pipeline: understand_media fallbacks
# --------------------------------------------------------------------------- #

_CARD = {"name": "Deri Çanta", "category": "çanta", "color": "kahve",
         "material": "deri", "description": "Güzel çanta."}


def _photo(tmp_path, name="p.jpg"):
    p = tmp_path / name
    Image.new("RGB", (64, 64), "red").save(p)
    return str(p)


def test_understand_media_sam_failure_falls_back_to_whole_image(monkeypatch, tmp_path):
    def boom(img, concept):
        raise RuntimeError("SAM down")
    monkeypatch.setattr(meta, "segment", boom)
    monkeypatch.setattr(pipeline, "describe_crop", lambda crop: dict(_CARD))
    ctx = pipeline.understand_media("ekle", None, [_photo(tmp_path)], concept="handbag",
                                    out_dir=tmp_path)
    assert len(ctx.products) == 1 and ctx.products[0].sam_confidence is None
    assert Path(ctx.products[0].crop_path).exists()
    assert any("segmentation failed" in n for n in ctx.notes)


def test_understand_media_no_boxes_falls_back_to_whole_image(monkeypatch, tmp_path):
    monkeypatch.setattr(meta, "segment", lambda img, concept: [])
    monkeypatch.setattr(pipeline, "describe_crop", lambda crop: dict(_CARD))
    ctx = pipeline.understand_media("ekle", None, [_photo(tmp_path)], concept="handbag",
                                    out_dir=tmp_path)
    assert len(ctx.products) == 1 and ctx.products[0].sam_confidence is None
    assert any("SAM found no" in n for n in ctx.notes)


def test_understand_media_skips_unreadable_photo(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "describe_crop", lambda crop: dict(_CARD))
    ctx = pipeline.understand_media("ekle", None, [str(tmp_path / "nope.jpg")], concept="x",
                                    out_dir=tmp_path)
    assert ctx.products == [] and any("could not be opened" in n for n in ctx.notes)


# --------------------------------------------------------------------------- #
# vision: describe_crop clamp
# --------------------------------------------------------------------------- #

def test_describe_crop_clamps_name_and_category(monkeypatch):
    monkeypatch.setattr(meta, "describe_image", lambda img, prompt: (
        '{"name": "Çok Uzun İsimli Güzel Deri Çanta Modeli", "category": "mobilya", '
        '"color": "kırmızı", "material": "deri", "description": "x"}'))
    info = pipeline.describe_crop(Image.new("RGB", (8, 8)))
    assert info["name"] == "Çok Uzun İsimli Güzel Deri"
    assert info["category"] == "diğer"


def test_describe_crop_keeps_valid_category_case_insensitive(monkeypatch):
    monkeypatch.setattr(meta, "describe_image", lambda img, prompt: (
        '{"name": "Midi Elbise", "category": "Elbise"}'))
    info = pipeline.describe_crop(Image.new("RGB", (8, 8)))
    assert (info["name"], info["category"]) == ("Midi Elbise", "elbise")


def test_describe_crop_non_json_still_falls_back(monkeypatch):
    monkeypatch.setattr(meta, "describe_image", lambda img, prompt: "güzel çanta, al bence")
    info = pipeline.describe_crop(Image.new("RGB", (8, 8)))
    assert (info["name"], info["category"]) == ("Yeni Ürün", "diğer")


# --------------------------------------------------------------------------- #
# Muse Image: extra_body retry
# --------------------------------------------------------------------------- #

def _png_b64():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _fake_images(monkeypatch, method, outcomes):
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        nxt = outcomes[len(calls) - 1]
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(meta, "client",
                        lambda: SimpleNamespace(images=SimpleNamespace(**{method: fake_call})))
    return calls


def test_generate_visual_edit_retries_size_only_on_400(monkeypatch, tmp_path):
    ok = SimpleNamespace(data=[SimpleNamespace(b64_json=_png_b64())])
    calls = _fake_images(monkeypatch, "edit", [_bad_request(400), ok])
    out = meta.generate_campaign_visual([Image.new("RGB", (8, 8))], "p", tmp_path / "v.png")
    assert out.exists() and len(calls) == 2
    assert calls[0]["extra_body"]["output_format"] == "png"
    assert calls[1]["extra_body"] == {"size": "1024x1024"}


def test_generate_visual_generate_retries_size_only_on_400(monkeypatch, tmp_path):
    ok = SimpleNamespace(data=[SimpleNamespace(b64_json=_png_b64())])
    calls = _fake_images(monkeypatch, "generate", [_bad_request(400), ok])
    out = meta.generate_campaign_visual([], "p", tmp_path / "v.png")
    assert out.exists() and len(calls) == 2
    assert calls[1]["extra_body"] == {"size": "1024x1024"}


def test_generate_visual_reraises_non_400(monkeypatch, tmp_path):
    calls = _fake_images(monkeypatch, "generate", [_bad_request(500)])
    with pytest.raises(BadRequestError):
        meta.generate_campaign_visual([], "p", tmp_path / "v.png")
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# scripts/check_api.py: per-step PASS/FAIL, never stops early
# --------------------------------------------------------------------------- #

def _load_check_api():
    path = Path(__file__).resolve().parent.parent / "scripts" / "check_api.py"
    spec = importlib.util.spec_from_file_location("check_api", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_api"] = mod
    spec.loader.exec_module(mod)
    return mod


check_api = _load_check_api()


def _patch_check_api(monkeypatch, fail_on=()):
    ids = [settings.orchestrator_model, settings.image_model, settings.sam_model]
    monkeypatch.setattr(meta, "client", lambda: SimpleNamespace(
        models=SimpleNamespace(list=lambda: SimpleNamespace(
            data=[SimpleNamespace(id=i) for i in ids]))))

    def maybe(step, fn):
        def wrap(*a, **k):
            if step in fail_on:
                raise RuntimeError(f"{step} down")
            return fn(*a, **k)
        return wrap

    monkeypatch.setattr(meta, "chat", maybe("chat", lambda *a, **k: "Merhaba!"))
    monkeypatch.setattr(meta, "transcribe", maybe("transcribe", lambda *a, **k: {
        "transcript": "Fular ekle", "audioDurationMs": 1500}))
    monkeypatch.setattr(meta, "load_image", lambda p: Image.new("RGB", (16, 16)))
    monkeypatch.setattr(meta, "segment", maybe("segment", lambda img, c: []))
    monkeypatch.setattr(meta, "describe_image", maybe("describe", lambda img, p: "güzel çanta"))


def test_check_api_reports_fail_and_continues(monkeypatch, tmp_path, capsys):
    _patch_check_api(monkeypatch, fail_on=("chat",))
    monkeypatch.chdir(tmp_path)
    rc = check_api.main(["--audio", "n.ogg", "--image", "p.jpg", "--concept", "bag"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL: RuntimeError: chat down" in out and "PASS" in out
    assert "Fular ekle" in out        # step 3 ran even though step 2 failed
    assert "1 check(s) FAILED" in out


def test_check_api_all_pass(monkeypatch, tmp_path, capsys):
    _patch_check_api(monkeypatch)
    monkeypatch.chdir(tmp_path)
    rc = check_api.main(["--audio", "n.ogg", "--image", "p.jpg"])
    out = capsys.readouterr().out
    assert rc == 0 and "FAIL" not in out
    assert "All requested checks passed." in out
