"""Business time. Merchants are in Türkiye; Modal containers run on UTC.

Türkiye has been on a fixed UTC+3 since 2016, and the gateway's own time helpers assume the same offset. A fixed
offset also needs no tzdata in the container image.

Naive results on purpose: plans carry naive wall-clock strings ("2026-11-01T15:00"), so the comparisons stay naive."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

TR = timezone(timedelta(hours=3))


def now_tr() -> datetime:
    return datetime.now(TR).replace(tzinfo=None)


def today_tr() -> date:
    return now_tr().date()
