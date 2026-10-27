"""Central configuration. Everything comes from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # Meta Model API
    api_key: str = field(default=os.environ.get("MODEL_API_KEY", ""), repr=False)
    base_url: str = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")

    # Model IDs (from https://dev.meta.ai/docs/models). Standard mode only:
    # content sent in Standard mode is NOT used to train Meta models.
    orchestrator_model: str = os.environ.get("ORCHESTRATOR_MODEL", "muse-spark-1.3")
    vision_model: str = os.environ.get("VISION_MODEL", "muse-spark-1.3")
    image_model: str = os.environ.get("IMAGE_MODEL", "muse-image-1.0")
    sam_model: str = os.environ.get("SAM_MODEL", "sam-3.1")
    asr_model: str = os.environ.get("ASR_MODEL", "muse-voice-transcribe-1.0")

    # Backend: "mock" (self-contained, for judges) or "kedd" (adapter, needs KEDD creds)
    backend: str = os.environ.get("BACKEND", "mock")
    mock_business: str = os.environ.get("MOCK_BUSINESS", "butik")   # butik | salon
    kedd_api_url: str = os.environ.get("KEDD_API_URL", "")
    kedd_agent_secret: str = field(default=os.environ.get("KEDD_AGENT_SECRET", ""), repr=False)
    merchant_phone: str = os.environ.get("MERCHANT_PHONE", "")      # E.164, identifies the merchant to KEDD

    # Paths (overridable so the Modal deployment can point them at a persistent volume)
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", ROOT / "data")))
    out_dir: Path = field(default_factory=lambda: Path(os.environ.get("OUT_DIR", ROOT / "out")))

    # WhatsApp Cloud API (dedicated Meta dev app + test number; KEDD's own app is not touched)
    graph_api: str = os.environ.get("GRAPH_API", "https://graph.facebook.com/v24.0")
    meta_app_secret: str = field(default=os.environ.get("META_APP_SECRET", ""), repr=False)
    wa_phone_number_id: str = os.environ.get("WA_PHONE_NUMBER_ID", "")
    wa_access_token: str = field(default=os.environ.get("WA_ACCESS_TOKEN", ""), repr=False)
    wa_verify_token: str = field(default=os.environ.get("WA_VERIFY_TOKEN", ""), repr=False)
    wa_allowed_senders: frozenset[str] = field(default_factory=lambda: frozenset(
        s.strip().lstrip("+") for s in os.environ.get("WA_ALLOWED_SENDERS", "").split(",") if s.strip()))

    # Guard-rails for the executor (business rules, enforced in code, not by the model)
    max_discount_pct: int = int(os.environ.get("MAX_DISCOUNT_PCT", "70"))
    max_campaign_recipients: int = int(os.environ.get("MAX_CAMPAIGN_RECIPIENTS", "500"))

    # Agent loop
    max_agent_turns: int = int(os.environ.get("MAX_AGENT_TURNS", "12"))
    reasoning_effort: str = os.environ.get("REASONING_EFFORT", "medium")


settings = Settings()


def require_api_key() -> str:
    if not settings.api_key:
        raise RuntimeError(
            "MODEL_API_KEY is not set. Create one at https://dev.meta.ai → API keys, "
            "then put it in .env (see .env.example)."
        )
    return settings.api_key
