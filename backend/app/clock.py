from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def sg_tz() -> tzinfo:
    """Singapore time. Falls back to a fixed UTC+8 if tz data is missing (e.g. Windows without tzdata)."""
    try:
        return ZoneInfo(os.getenv("TZ", "Asia/Singapore"))
    except (ZoneInfoNotFoundError, ValueError):
        return timezone(timedelta(hours=8))


def sg_now() -> datetime:
    return datetime.now(sg_tz())


def sg_today() -> date:
    return sg_now().date()
