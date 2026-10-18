from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import json
from statistics import mean
from typing import Any

from .clock import sg_now

EXPECTED_RAIN_READINGS = 288  # one 5-minute total per station per day
COMPLETE_FRACTION = 0.9


@dataclass(frozen=True)
class AggregatedReading:
    rain_24h_mm: float | None
    rain_48h_mm: float | None
    temp_c: float | None
    psi_24h: float | None
    pm25: float | None
    rain_reading_count: int
    temp_reading_count: int
    psi_reading_count: int
    pm25_reading_count: int
    partial: bool
    stations: dict[str, list[str]]
    raw_json: str


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _in_date(timestamp: str, target: date) -> bool:
    return _timestamp(timestamp).date() == target


def _rain_totals(payload: dict[str, Any], target: date, station_ids: set[str]) -> tuple[dict[str, float], int, str | None, str | None]:
    totals: dict[str, float] = defaultdict(float)
    count = 0
    timestamps: list[str] = []
    for reading in (payload.get("data") or {}).get("readings", []):
        timestamp = reading.get("timestamp")
        if not timestamp or not _in_date(timestamp, target):
            continue
        for item in reading.get("data", []):
            station_id = item.get("stationId")
            if station_id in station_ids and item.get("value") is not None:
                totals[station_id] += float(item["value"])
                count += 1
                timestamps.append(timestamp)
    return totals, count, min(timestamps) if timestamps else None, max(timestamps) if timestamps else None


def _rain_complete(count: int, stations: int) -> bool:
    return stations > 0 and count >= EXPECTED_RAIN_READINGS * stations * COMPLETE_FRACTION


def daily_rain_total(payload: dict[str, Any], target: date, station_ids: set[str]) -> tuple[float | None, bool]:
    """(mean-of-stations rainfall for the day, whether the day's coverage is complete)."""
    totals, count, _, _ = _rain_totals(payload, target, station_ids)
    if not totals:
        return None, False
    return mean(totals.values()), _rain_complete(count, len(totals))


def aggregate(
    target: date,
    rainfall: dict[str, Any],
    temperature: dict[str, Any],
    psi: dict[str, Any],
    pm25: dict[str, Any],
    station_ids: list[str],
    previous_rain_24h: float | None = None,
    temp_sample_minutes: int | None = None,
    now: datetime | None = None,
) -> AggregatedReading:
    """Combine one day of API payloads. Missing data stays None; nothing is estimated."""
    configured = set(station_ids)
    rain_totals, rain_count, rain_first, rain_last = _rain_totals(rainfall, target, configured)
    rain_stations = sorted(rain_totals)
    rain_24h = mean(rain_totals.values()) if rain_totals else None
    day_not_over = target >= (now or sg_now()).date()
    partial = day_not_over or not _rain_complete(rain_count, len(rain_stations))

    rain_48h = rain_24h + previous_rain_24h if rain_24h is not None and previous_rain_24h is not None else None

    temp_values: list[float] = []
    temp_stations: set[str] = set()
    temp_timestamps: list[str] = []
    for reading in (temperature.get("data") or {}).get("readings", []):
        timestamp = reading.get("timestamp")
        if not timestamp or not _in_date(timestamp, target):
            continue
        for item in reading.get("data", []):
            if item.get("stationId") in configured and item.get("value") is not None:
                temp_values.append(float(item["value"]))
                temp_stations.add(item["stationId"])
                temp_timestamps.append(timestamp)

    psi_values: list[tuple[datetime, float]] = []
    for item in (psi.get("data") or {}).get("items", []):
        timestamp = item.get("timestamp")
        value = ((item.get("readings") or {}).get("psi_twenty_four_hourly") or {}).get("north")
        if timestamp and _in_date(timestamp, target) and value is not None:
            psi_values.append((_timestamp(timestamp), float(value)))

    pm_values: list[float] = []
    for item in (pm25.get("data") or {}).get("items", []):
        timestamp = item.get("timestamp")
        value = ((item.get("readings") or {}).get("pm25_one_hourly") or {}).get("north")
        if timestamp and _in_date(timestamp, target) and value is not None:
            pm_values.append(float(value))

    raw = {
        "rainfall": {
            "stations": rain_stations,
            "reading_count": rain_count,
            "first_timestamp": rain_first,
            "last_timestamp": rain_last,
            "previous_day_mm": previous_rain_24h,
        },
        "air_temperature": {
            "stations": sorted(temp_stations),
            "reading_count": len(temp_values),
            "sample_minutes": temp_sample_minutes,
            "first_timestamp": min(temp_timestamps) if temp_timestamps else None,
            "last_timestamp": max(temp_timestamps) if temp_timestamps else None,
        },
        "psi": {"region": "north", "reading_count": len(psi_values)},
        "pm25": {"region": "north", "reading_count": len(pm_values)},
    }
    return AggregatedReading(
        rain_24h_mm=rain_24h,
        rain_48h_mm=rain_48h,
        temp_c=mean(temp_values) if temp_values else None,
        psi_24h=max(psi_values, key=lambda item: item[0])[1] if psi_values else None,
        pm25=mean(pm_values) if pm_values else None,
        rain_reading_count=rain_count,
        temp_reading_count=len(temp_values),
        psi_reading_count=len(psi_values),
        pm25_reading_count=len(pm_values),
        partial=partial,
        stations={
            "rainfall": rain_stations,
            "air_temperature": sorted(temp_stations),
            "psi": ["north"] if psi_values else [],
            "pm25": ["north"] if pm_values else [],
        },
        raw_json=json.dumps(raw, separators=(",", ":")),
    )
