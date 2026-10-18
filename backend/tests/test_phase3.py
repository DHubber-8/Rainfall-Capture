from datetime import date
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.fetching import fetch_date, fetch_range
from app.main import app
from app.models import Reading


FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8-sig"))


class FixtureClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def fetch_all(self, endpoint, target):
        if target == "2026-12-31":
            return load("empty-2026-12-31.json")
        if endpoint == "rainfall":
            return load("rainfall-2026-10-15.json")
        if endpoint == "air-temperature":
            return load("air-temperature-2026-10-15.json")
        if endpoint == "psi":
            return load("psi-2026-10-15.json")
        return load("pm25-2026-10-15.json")


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase3.db').as_posix()}")
    from app.db import Base
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.mark.asyncio
async def test_fetch_upserts_api_fields_and_preserves_manual_fields(db):
    reading = Reading(date=date(2026, 10, 15), haze="Y", rain_after_haze="N", sampling_notes="keep")
    db.add(reading)
    db.commit()
    result = await fetch_date(db, date(2026, 10, 15), FixtureClient(), delay=0)
    saved = db.get(Reading, date(2026, 10, 15))
    assert result.status == "partial"
    assert saved.rain_24h_mm is not None
    assert saved.haze == "Y"
    assert saved.rain_after_haze == "N"
    assert saved.sampling_notes == "keep"


@pytest.mark.asyncio
async def test_fetch_empty_day_does_not_create_row(db):
    result = await fetch_date(db, date(2026, 12, 31), FixtureClient(), delay=0)
    assert result.status == "no-data"
    assert db.get(Reading, date(2026, 12, 31)) is None


@pytest.mark.asyncio
async def test_fetch_range_is_capped(db):
    with pytest.raises(ValueError, match="31 days"):
        await fetch_range(db, date(2026, 1, 1), date(2026, 2, 1), lambda: FixtureClient(), delay=0)


def test_fetch_endpoint_rejects_ranges_over_31_days(monkeypatch):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.post("/api/fetch?from=2026-01-01&to=2026-02-01")
    assert response.status_code == 400
    assert "31 days" in response.json()["detail"]
