"""Closing the loop: what happened, in the merchant's language."""

from __future__ import annotations

import json
import re
from typing import Any

from . import meta_client as meta
from .tools.backend import Backend


def build_report(steps: list[dict[str, Any]], backend: Backend) -> dict[str, Any]:
    return {"steps": steps, "backend": backend.report()}


def _report_numbers(node: Any, found: set[str]) -> None:
    """Every digit run in the report, plus the length of every list (the summary may count things)."""
    if isinstance(node, dict):
        for value in node.values():
            _report_numbers(value, found)
    elif isinstance(node, (list, tuple)):
        found.add(str(len(node)))
        for value in node:
            _report_numbers(value, found)
    elif not isinstance(node, bool):
        found.update(re.findall(r"\d+", str(node)))


def numbers_verified(summary: str, report: dict[str, Any]) -> bool:
    """The summary is model prose about things that really happened to real customers. The prompt tells it to
    use the report's numbers; this checks that it did. "1.500" counts as 1500, "127,5" as 127 and 5."""
    known: set[str] = set()
    _report_numbers(report, known)
    for count in _action_counts(report):
        known.add(str(count))
    for number in re.findall(r"\d+(?:[.,]\d+)*", summary):
        joined = re.sub(r"[.,]", "", re.sub(r"[.,]0+$", "", number))
        if joined not in known and not all(part in known for part in re.split(r"[.,]", number)):
            return False
    return True


def _action_counts(report: dict[str, Any]) -> set[int]:
    steps = [s for s in report.get("steps") or [] if isinstance(s, dict)]
    actions = [s.get("action") for s in steps]
    return {actions.count(a) for a in actions}


def summarize_for_merchant(report: dict[str, Any], lang: str = "tr") -> str:
    """3-4 line WhatsApp message in the owner's language. Numbers must come from the report only."""
    # Seen live on KEDD: the backend lists are empty there (the gateway has no session diff), and the model
    # read that as "nothing changed" right after a step succeeded. The steps are what happened.
    truth = ("The \"steps\" list is the source of truth: every step with status \"ok\" really happened "
             "just now. The \"backend\" lists may be empty; that never means nothing was done. "
             "Do not greet. ")
    if lang == "en":
        prompt = ("Write a short English WhatsApp message (max 4 lines, informal, one emoji max) telling a "
                  "small business owner what was done, using ONLY the numbers in this JSON (items added, "
                  "discounts, campaigns). Mention only things that actually happened; skip empty lists. If a "
                  "campaign was sent and its engagement is marked simulated, say 'simulated' once. No "
                  "markdown.\n\n")
    else:
        prompt = ("Write a short Turkish WhatsApp message (max 4 lines, informal 'sen', one emoji max) "
                  "telling a small business owner what was done, using ONLY the numbers in this JSON "
                  "(items added, discounts, campaigns). Mention only things that actually happened; skip "
                  "empty lists. If a campaign was sent and its engagement is marked simulated, say "
                  "'simülasyon' once. No markdown.\n\n")
    return meta.chat(truth + prompt + json.dumps(report, ensure_ascii=False), effort="minimal")


def in_owner_language(text: str, lang: str) -> str:
    """Model prose for the owner, guaranteed in the owner's language.

    Seen live: an English owner asked "What's waiting for me today?" and got a Turkish brief — the KEDD data
    is Turkish and outweighed the prompt. A wrong-language answer is translated once; the translation is used
    only if it keeps every number, so a fact can never change on the way."""
    from . import i18n
    from .config import settings
    if not text.strip() or i18n.detect_lang(text, lang) == lang or not settings.api_key:
        return text
    target = "English" if lang == "en" else "Turkish"
    try:
        out = meta.chat(f"Translate this WhatsApp message to {target}. Keep every number, name, emoji, line "
                        "break and bullet exactly; keep product and business names as they are. Output only "
                        "the translation.\n\n" + text, effort="minimal").strip()
    except Exception:  # noqa: BLE001 — the original answer is still true, only in the other language
        return text
    digits = lambda s: sorted(re.findall(r"\d+", s))  # noqa: E731
    return out if out and digits(out) == digits(text) else text
