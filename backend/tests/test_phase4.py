"""Regression tests for the fetch failures: crashes, network errors, history gaps, request volume."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.aggregation import aggregate
from app.client import DataGovClient, DataGovError
from app.db import Base
from app.fetching import dates_needing_fetch, fetch_date, fetch_dates
from app.models import FetchAttempt, Reading

FIXTURES = Path(__file__).parent / "fixtures"
load = lambda name: json.loads((FIXTURES / name).read_text(encoding="utf-8-sig"))


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


class Recorder:
    """Stands in for DataGovClient and counts requests."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def fetch_all(self, endpoint, target):
        self.calls.append((endpoint, target))
        if self.fail_on == endpoint:
            raise self.fail_exc
        if target != "2026-10-15":
            return load("empty-2026-12-31.json")
        return load(f"{endpoint}-2026-10-15.json")

    async def fetch_snapshots(self, endpoint, target, step_minutes, until=None):
        self.calls.append((endpoint, f"snap-{step_minutes}"))
        return load("air-temperature-2026-10-15.json")


def test_aggregate_does_not_crash_when_configured_stations_did_not_report():
    # Used to raise UnboundLocalError (previous_count) whenever the day had no rainfall for our stations.
    result = aggregate(
        date(2026, 10, 15), load("rainfall-2026-10-15.json"), load("air-temperature-2026-10-15.json"),
        load("psi-2026-10-15.json"), load("pm25-2026-10-15.json"), ["S999"], previous_rain_24h=3.0,
    )
    assert result.rain_24h_mm is None and result.rain_48h_mm is None and result.partial is True


def test_rain_48h_adds_previous_day_and_stays_none_without_it():
    args = (date(2026, 10, 15), load("rainfall-2026-10-15.json"), load("air-temperature-2026-10-15.json"),
            load("psi-2026-10-15.json"), load("pm25-2026-10-15.json"), ["S210", "S227", "S104"])
    today = aggregate(*args)
    assert today.rain_48h_mm is None
    both = aggregate(*args, previous_rain_24h=2.5)
    assert both.rain_48h_mm == pytest.approx(today.rain_24h_mm + 2.5)


@pytest.mark.asyncio
async def test_network_errors_become_data_gov_errors():
    class Boom:
        async def get(self, *a, **k):
            raise httpx.ReadTimeout("slow")

        async def aclose(self):
            pass

    async with DataGovClient(client=Boom(), max_retries=1, request_interval=0) as client:
        with pytest.raises(DataGovError, match="network error"):
            await client.fetch_page("rainfall", "2026-10-15")


@pytest.mark.asyncio
async def test_non_json_error_body_is_a_clean_error():
    class Html:
        async def get(self, *a, **k):
            return httpx.Response(403, text="<html>blocked</html>")

        async def aclose(self):
            pass

    async with DataGovClient(client=Html(), request_interval=0) as client:
        with pytest.raises(DataGovError, match="HTTP 403"):
            await client.fetch_page("psi", "2026-10-15")


@pytest.mark.asyncio
async def test_retry_after_header_is_respected(monkeypatch):
    responses = [httpx.Response(429, headers={"Retry-After": "7"}, json={}), httpx.Response(200, json={"code": 0, "data": {"items": []}})]
    sleeps = []

    class Fake:
        async def get(self, *a, **k):
            return responses.pop(0)

        async def aclose(self):
            pass

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.client.asyncio.sleep", fake_sleep)
    async with DataGovClient(client=Fake(), request_interval=0) as client:
        await client.fetch_page("psi", "2026-10-15")
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_snapshots_sample_every_step_and_dedupe():
    seen = []

    class Fake:
        async def get(self, url, params, headers):
            seen.append(params["date"])
            return httpx.Response(200, json={"code": 0, "data": {"stations": [], "readings": [
                {"timestamp": "2026-10-15T12:00:00+08:00", "data": [{"stationId": "S104", "value": 30}]}]}})

        async def aclose(self):
            pass

    async with DataGovClient(client=Fake(), request_interval=0) as client:
        payload = await client.fetch_snapshots("air-temperature", date(2026, 10, 15), 30)
    assert len(seen) == 48 and seen[0] == "2026-10-15T00:00:00" and seen[-1] == "2026-10-15T23:30:00"
    assert len(payload["data"]["readings"]) == 1  # identical timestamps collapse


@pytest.mark.asyncio
async def test_one_failing_day_does_not_abort_the_range(db):
    recorder = Recorder(fail_on="psi")
    recorder.fail_exc = DataGovError("psi rate limit persisted after retries")
    results = await fetch_dates(db, [date(2026, 10, 14), date(2026, 10, 15)], lambda: recorder)
    assert [r.status for r in results] == ["error", "error"]
    assert db.get(FetchAttempt, date(2026, 10, 14)).status == "error"


@pytest.mark.asyncio
async def test_unexpected_exception_is_reported_not_raised(db):
    recorder = Recorder(fail_on="rainfall")
    recorder.fail_exc = ValueError("bad payload")
    result = await fetch_date(db, date(2026, 10, 15), recorder)
    assert result.status == "error" and "ValueError" in result.message


@pytest.mark.asyncio
async def test_old_empty_day_costs_three_requests_and_skips_temperature(db):
    recorder = Recorder()
    result = await fetch_date(db, date(2026, 1, 5), recorder)
    assert result.status == "no-data"
    assert [c[0] for c in recorder.calls] == ["rainfall", "psi", "pm25"]
    assert db.get(Reading, date(2026, 1, 5)) is None


@pytest.mark.asyncio
async def test_temperature_is_sampled_every_30_minutes(db):
    recorder = Recorder()
    await fetch_date(db, date(2026, 10, 15), recorder)
    assert ("air-temperature", "snap-30") in recorder.calls
    assert not any(c[0] == "air-temperature" and not c[1].startswith("snap") for c in recorder.calls)


@pytest.mark.asyncio
async def test_previous_day_total_is_reused_from_the_database(db):
    db.add(Reading(date=date(2026, 10, 14), rain_24h_mm=4.0, partial=False, fetched_at=datetime.now(timezone.utc)))
    db.commit()
    recorder = Recorder()
    await fetch_date(db, date(2026, 10, 15), recorder)
    rainfall_calls = [c for c in recorder.calls if c[0] == "rainfall"]
    assert rainfall_calls == [("rainfall", "2026-10-15")]  # no second fetch for the 14th
    saved = db.get(Reading, date(2026, 10, 15))
    assert saved.rain_48h_mm == pytest.approx(saved.rain_24h_mm + 4.0)


def test_backfill_rules(db):
    long_ago = datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)
    db.add_all([
        Reading(date=date(2026, 9, 25), rain_24h_mm=1.0, partial=False, fetched_at=long_ago),   # complete -> skip
        Reading(date=date(2026, 9, 26), haze="Y"),                                              # manual-only row -> fetch
        Reading(date=date(2026, 9, 27), rain_24h_mm=1.0, partial=True, fetched_at=datetime(2026, 9, 27, 4, 0, tzinfo=timezone.utc)),  # partial, fetched mid-day -> retry
        Reading(date=date(2026, 9, 28), rain_24h_mm=1.0, partial=True, fetched_at=datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc)),  # partial but fetched after the day ended -> final
    ])
    db.add(FetchAttempt(date=date(2026, 9, 29), status="no-data", message=None, attempted_at=datetime.now(timezone.utc)))
    db.add(FetchAttempt(date=date(2026, 9, 30), status="error", message="x", attempted_at=datetime.now(timezone.utc)))
    db.commit()
    assert dates_needing_fetch(db, date(2026, 9, 25), date(2026, 9, 30)) == [date(2026, 9, 26), date(2026, 9, 27), date(2026, 9, 30)]


@pytest.mark.asyncio
async def test_only_one_fetch_job_runs_at_a_time(tmp_path, monkeypatch):
    import asyncio
    from app import jobs

    class Slow(Recorder):
        async def fetch_all(self, endpoint, target):
            await asyncio.sleep(0.2)
            return await super().fetch_all(endpoint, target)

    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(jobs, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    jobs.JOBS.clear()
    job = jobs.start_job("fetch", [date(2026, 1, 5)], lambda: Slow())
    with pytest.raises(jobs.JobBusy):
        jobs.start_job("fetch", [date(2026, 1, 6)], lambda: Slow())
    for _ in range(50):
        if job.status != "running":
            break
        await asyncio.sleep(0.1)
    assert job.status == "done" and job.completed == 1
    assert jobs.start_job("fetch", []).status == "done"  # an empty backfill finishes immediately
