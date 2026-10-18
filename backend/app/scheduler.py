from __future__ import annotations

from datetime import date, datetime, timedelta
import asyncio
import logging
import os
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .client import DataGovClient
from .db import SessionLocal
from .fetching import FetchResult, fetch_date, fetch_range
from .models import Reading

logger = logging.getLogger(__name__)


def singapore_today() -> date:
    return datetime.now(ZoneInfo(os.getenv("TZ", "Asia/Singapore"))).date()


async def fetch_yesterday() -> None:
    target = singapore_today() - timedelta(days=1)
    with SessionLocal() as db:
        async with DataGovClient() as client:
            result = await fetch_date(db, target, client)
    logger.info("scheduled fetch complete", extra={"date": target.isoformat(), "status": result.status})


async def backfill_missing() -> list[FetchResult]:
    end = singapore_today() - timedelta(days=1)
    configured_start = os.getenv("BACKFILL_FROM", "2026-01-01")
    start = date.fromisoformat(configured_start)
    with SessionLocal() as db:
        existing = set(db.scalars(select(Reading.date).where(Reading.date.between(start, end))))
    missing = [start + timedelta(days=offset) for offset in range((end - start).days + 1) if start + timedelta(days=offset) not in existing]
    results: list[FetchResult] = []
    for index in range(0, len(missing), 31):
        batch = missing[index : index + 31]
        if not batch:
            continue
        with SessionLocal() as db:
            results.extend(await fetch_range(db, batch[0], batch[-1]))
    return results


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(os.getenv("TZ", "Asia/Singapore")))
    scheduler.add_job(fetch_yesterday, "cron", hour=0, minute=30, id="fetch-yesterday", replace_existing=True)
    return scheduler
