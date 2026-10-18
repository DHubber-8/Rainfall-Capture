from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from .aggregation import AggregatedReading, aggregate
from .client import DataGovClient, DataGovError
from .models import Reading


@dataclass(frozen=True)
class FetchResult:
    date: date
    status: str
    message: str | None = None
    partial: bool = False


def configured_stations() -> list[str]:
    return [value.strip() for value in os.getenv("WOODLANDS_STATIONS", "S210,S227,S104").split(",") if value.strip()]


def _is_no_data(payload: dict[str, Any]) -> bool:
    return payload.get("code") == 17 or payload.get("data") is None


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
    reading.raw_json = result.raw_json
    reading.fetched_at = datetime.now(timezone.utc)
    reading.updated_at = datetime.now(timezone.utc)


async def fetch_date(
    db: Session,
    target: date,
    client: DataGovClient,
    stations: list[str] | None = None,
    delay: float = 1.0,
) -> FetchResult:
    station_ids = stations or configured_stations()
    try:
        payloads: dict[str, dict[str, Any]] = {}
        for endpoint in ("rainfall", "air-temperature", "psi", "pm25"):
            payloads[endpoint] = await client.fetch_all(endpoint, target.isoformat())
            if delay:
                await asyncio.sleep(delay)

        if all(_is_no_data(payload) for payload in payloads.values()):
            return FetchResult(target, "no-data", "No readings were returned by the API")

        previous_rainfall = await client.fetch_all("rainfall", (target - timedelta(days=1)).isoformat())
        result = aggregate(
            target,
            payloads["rainfall"],
            payloads["air-temperature"],
            payloads["psi"],
            payloads["pm25"],
            station_ids,
            previous_rainfall=None if _is_no_data(previous_rainfall) else previous_rainfall,
        )
        _upsert_api_fields(db, target, result)
        db.commit()
        return FetchResult(target, "partial" if result.partial else "fetched", partial=result.partial)
    except DataGovError as exc:
        db.rollback()
        return FetchResult(target, "error", str(exc))
    except Exception:
        db.rollback()
        raise


async def fetch_range(
    db: Session,
    start: date,
    end: date,
    client_factory: Callable[[], DataGovClient] | None = None,
    stations: list[str] | None = None,
    delay: float = 1.0,
) -> list[FetchResult]:
    if start > end:
        raise ValueError("from must not be after to")
    if (end - start).days + 1 > 31:
        raise ValueError("fetch range cannot exceed 31 days")

    factory = client_factory or DataGovClient
    results: list[FetchResult] = []
    async with factory() as client:
        current = start
        while current <= end:
            results.append(await fetch_date(db, current, client, stations, delay))
            current += timedelta(days=1)
    return results
