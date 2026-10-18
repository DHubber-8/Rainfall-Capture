from __future__ import annotations

from datetime import date, timedelta
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .clock import sg_today, sg_tz
from .db import SessionLocal
from .fetching import dates_needing_fetch
from .jobs import JobBusy, start_job

logger = logging.getLogger(__name__)


def backfill_start() -> date:
    return date.fromisoformat(os.getenv("BACKFILL_FROM") or "2026-09-25")


def missing_dates(start: date | None = None) -> list[date]:
    end = sg_today() - timedelta(days=1)
    with SessionLocal() as db:
        return dates_needing_fetch(db, start or backfill_start(), end)


def start_gap_fill(kind: str, start: date | None = None):
    return start_job(kind, missing_dates(start))


async def nightly_gap_fill() -> None:
    """Fills yesterday plus any gaps in the last 14 days (including days that were only partial)."""
    start = max(backfill_start(), sg_today() - timedelta(days=14))
    try:
        job = start_gap_fill("nightly", start)
        logger.info("nightly gap fill started: %s days", job.total)
    except JobBusy:
        logger.info("nightly gap fill skipped: another fetch is running")


async def startup_backfill() -> None:
    try:
        job = start_gap_fill("backfill")
        logger.info("startup backfill started: %s days", job.total)
    except JobBusy:
        pass


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=sg_tz())
    scheduler.add_job(nightly_gap_fill, "cron", hour=0, minute=30, id="nightly-gap-fill", replace_existing=True)
    return scheduler
