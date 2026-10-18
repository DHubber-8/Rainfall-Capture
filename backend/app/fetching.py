from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
import os
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .aggregation import AggregatedReading, aggregate, daily_rain_total
from .clock import sg_now, sg_today, sg_tz
from .client import DataGovClient, DataGovError
from .models import FetchAttempt, Reading

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchResult:
    date: date
    status: str
    message: str | None = None
    partial: bool = False


def configured_stations() -> list[str]:
    return [value.strip() for value in os.getenv("WOODLANDS_STATIONS", "S210,S227,S104").split(",") if value.strip()]


def temp_sample_minutes() -> int:
    try:
        return max(1, int(os.getenv("TEMP_SAMPLE_MINUTES", "30")))
    except ValueError:
        return 30


def max_fetch_days() -> int:
    try:
        return max(1, int(os.getenv("MAX_FETCH_DAYS", "366")))
    except ValueError:
        return 366


def _is_no_data(payload: dict[str, Any]) -> bool:
    return payload.get("code") == 17 or payload.get("data") is None


def _record_attempt(db: Session, target: date, status: str, message: str | None) -> None:
    db.merge(FetchAttempt(date=target, status=status, message=message, attempted_at=datetime.now(timezone.utc)))


def _upsert_api_fields(db: Session, target: date, result: AggregatedReading) -> None:
    reading = db.get(Reading, target)
    if reading is None:
        reading = Reading(date=target)
        db.add(reading)
    reading.rain_24h_mm = result.rain_24h_mm
    reading.rain_48h_mm = result.rain_48h_mm
    reading.temp_c = result.temp_c
    reading.psi_24h = result.psi_24h
    reading.pm25 = result.pm25
    reading.rain_reading_count = result.rain_reading_count
    reading.temp_reading_count = result.temp_reading_count
    reading.psi_reading_count = result.psi_reading_count
    reading.pm25_reading_count = result.pm25_reading_count
    reading.partial = result.partial
    reading.raw_json = result.raw_json
    reading.fetched_at = datetime.now(timezone.utc)
    reading.updated_at = datetime.now(timezone.utc)


def _fill_next_day_48h(db: Session, target: date, result: AggregatedReading) -> None:
    """If tomorrow was fetched before today existed, its 48h total was NULL; now it can be completed."""
    if result.partial or result.rain_24h_mm is None:
        return
    following = db.get(Reading, target + timedelta(days=1))
    if following is not None and following.rain_24h_mm is not None and following.rain_48h_mm is None:
        following.rain_48h_mm = following.rain_24h_mm + result.rain_24h_mm


async def _previous_day_rain(db: Session, client: DataGovClient, target: date, station_ids: list[str]) -> float | None:
    previous_date = target - timedelta(days=1)
    stored = db.get(Reading, previous_date)
    if stored is not None and stored.rain_24h_mm is not None and not stored.partial:
        return stored.rain_24h_mm  # already fetched: no extra requests
    payload = await client.fetch_all("rainfall", previous_date.isoformat())
    if _is_no_data(payload):
        return None
    total, complete = daily_rain_total(payload, previous_date, set(station_ids))
    return total if complete else None


def _finish(db: Session, target: date, status: str, message: str | None, partial: bool = False) -> FetchResult:
    _record_attempt(db, target, status, message)
    db.commit()
    return FetchResult(target, status, message, partial)


async def fetch_date(
    db: Session,
    target: date,
    client: DataGovClient,
    stations: list[str] | None = None,
    delay: float = 0.0,
) -> FetchResult:
    """Fetch and store one day. Always returns a FetchResult; failures never escape to abort a range."""
    station_ids = stations or configured_stations()
    today = sg_today()
    if target > today:
        return FetchResult(target, "no-data", "Date is in the future")
    try:
        payloads: dict[str, dict[str, Any]] = {}
        # Rainfall values are 5-minute totals, so every page is needed for a correct daily sum (~12 requests).
        for endpoint in ("rainfall", "psi", "pm25"):
            payloads[endpoint] = await client.fetch_all(endpoint, target.isoformat())
            if delay:
                await asyncio.sleep(delay)

        if all(_is_no_data(payload) for payload in payloads.values()):
            return _finish(db, target, "no-data", "No readings were returned by the API")

        # Temperature is averaged, so a 30-minute sample is enough (one request per sample, no paging).
        sample = temp_sample_minutes()
        temperature = await client.fetch_snapshots(
            "air-temperature", target, sample, until=sg_now() if target == today else None
        )
        previous = await _previous_day_rain(db, client, target, station_ids)
        result = aggregate(
            target,
            payloads["rainfall"],
            temperature,
            payloads["psi"],
            payloads["pm25"],
            station_ids,
            previous_rain_24h=previous,
            temp_sample_minutes=sample,
        )
        _upsert_api_fields(db, target, result)
        _fill_next_day_48h(db, target, result)
        status = "partial" if result.partial else "fetched"
        return _finish(db, target, status, None, result.partial)
    except DataGovError as exc:
        db.rollback()
        return _finish(db, target, "error", str(exc))
    except Exception as exc:  # keep one bad day from killing the whole range
        db.rollback()
        logger.exception("unexpected error fetching %s", target)
        return _finish(db, target, "error", f"Unexpected error: {type(exc).__name__}: {exc}")


async def fetch_dates(
    db: Session,
    dates: list[date],
    client_factory: Callable[[], DataGovClient] | None = None,
    stations: list[str] | None = None,
    delay: float = 0.0,
    on_progress: Callable[[FetchResult], None] | None = None,
) -> list[FetchResult]:
    factory = client_factory or DataGovClient
    results: list[FetchResult] = []
    async with factory() as client:
        for target in sorted(dates):  # oldest first so each day can reuse the previous day's stored total
            result = await fetch_date(db, target, client, stations, delay)
            results.append(result)
            if on_progress:
                on_progress(result)
    return results


def date_span(start: date, end: date) -> list[date]:
    if start > end:
        raise ValueError("from must not be after to")
    limit = max_fetch_days()
    if (end - start).days + 1 > limit:
        raise ValueError(f"fetch range cannot exceed {limit} days")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def fetch_range(
    db: Session,
    start: date,
    end: date,
    client_factory: Callable[[], DataGovClient] | None = None,
    stations: list[str] | None = None,
    delay: float = 0.0,
    on_progress: Callable[[FetchResult], None] | None = None,
) -> list[FetchResult]:
    return await fetch_dates(db, date_span(start, end), client_factory, stations, delay, on_progress)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def dates_needing_fetch(db: Session, start: date, end: date) -> list[date]:
    """Dates in [start, end] that have no trustworthy stored data.

    Skipped: days stored complete, days that were fetched after they ended (a partial result is then
    permanent, e.g. a station outage), and no-data days attempted within NO_DATA_RETRY_DAYS.
    Retried: never fetched, errors, partial days fetched before the day ended, and rows that only hold manual notes.
    """
    if start > end:
        return []
    retry_days = int(os.getenv("NO_DATA_RETRY_DAYS", "7") or 7)
    now = datetime.now(timezone.utc)
    tz = sg_tz()
    done: set[date] = set()
    for day, partial, fetched_at in db.execute(
        select(Reading.date, Reading.partial, Reading.fetched_at).where(Reading.date.between(start, end))
    ):
        if fetched_at is None:
            continue
        day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
        if not partial or _utc(fetched_at) >= day_end:
            done.add(day)
    attempts = {item.date: item for item in db.scalars(select(FetchAttempt).where(FetchAttempt.date.between(start, end)))}

    missing: list[date] = []
    day = start
    while day <= end:
        attempt = attempts.get(day)
        recently_empty = (
            attempt is not None
            and attempt.status == "no-data"
            and now - _utc(attempt.attempted_at) < timedelta(days=retry_days)
        )
        if day not in done and not recently_empty:
            missing.append(day)
        day += timedelta(days=1)
    return missing
