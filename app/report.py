"""Closing the loop: what happened, in the merchant's language."""

from __future__ import annotations

import json
from typing import Any

from . import meta_client as meta
from .tools.backend import Backend


def build_report(steps: list[dict[str, Any]], backend: Backend) -> dict[str, Any]:
    return {"steps": steps, "backend": backend.report()}


def summarize_for_merchant(report: dict[str, Any]) -> str:
    """3-4 line Turkish WhatsApp message. Numbers must come from the report only."""
    return meta.chat(
        "Write a short Turkish WhatsApp message (max 4 lines, informal 'sen', one emoji max) telling a "
        "small business owner what was done, using ONLY the numbers in this JSON (items added, discounts, "
        "campaigns). Mention only things that actually happened; skip empty lists. If a campaign was sent and "
        "its engagement is marked simulated, say 'simülasyon' once. No markdown.\n\n"
        + json.dumps(report, ensure_ascii=False),
        effort="minimal",
    )
