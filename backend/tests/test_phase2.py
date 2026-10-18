"""Fixtures were captured from live data.gov.sg responses on 2026-10-16.
Sources: https://api-open.data.gov.sg/v2/real-time/api/{rainfall,air-temperature,psi,pm25}?date=2026-10-15
The empty fixture is the live response from
https://api-open.data.gov.sg/v2/real-time/api/rainfall?date=2026-12-31.
"""

from datetime import date
import json
from pathlib import Path

import httpx
import pytest

from app.aggregation import aggregate
from app.client import DataGovClient, DataGovError


FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8-sig"))


def test_real_fixture_aggregation_preserves_missing_as_none():
    result = aggregate(
        date(2026, 10, 15),
        load("rainfall-2026-10-15.json"),
        load("air-temperature-2026-10-15.json"),
        load("psi-2026-10-15.json"),
        load("pm25-2026-10-15.json"),
        ["S210", "S227", "S104"],
    )
    assert result.rain_24h_mm is not None
    assert result.temp_c is not None
    assert result.psi_24h is not None
    assert result.pm25 is not None
    assert result.rain_48h_mm is None
    assert result.partial is True
    assert result.rain_reading_count > 0
    assert "S210" in result.stations["rainfall"]
    assert result.psi_24h == 130


def test_empty_real_response_produces_gaps():
    empty = load("empty-2026-12-31.json")
    result = aggregate(date(2026, 12, 31), empty, empty, empty, empty, ["S210"])
    assert result.rain_24h_mm is None
    assert result.rain_48h_mm is None
    assert result.temp_c is None
    assert result.psi_24h is None
    assert result.pm25 is None
    assert result.rain_reading_count == 0


@pytest.mark.asyncio
async def test_client_follows_pagination(monkeypatch):
    responses = [
        httpx.Response(200, json={"code": 0, "data": {"readings": [{"timestamp": "a"}], "paginationToken": "next"}}),
        httpx.Response(200, json={"code": 0, "data": {"readings": [{"timestamp": "b"}]}}),
    ]

    class FakeClient:
        async def get(self, *args, **kwargs):
            return responses.pop(0)

        async def aclose(self):
            pass

    async with DataGovClient(client=FakeClient()) as client:
        payload = await client.fetch_all("rainfall", "2026-10-15")
    assert [item["timestamp"] for item in payload["data"]["readings"]] == ["a", "b"]


@pytest.mark.asyncio
async def test_client_retries_429(monkeypatch):
    responses = [
        httpx.Response(429, json={"code": 24}),
        httpx.Response(200, json={"code": 0, "data": {"items": []}}),
    ]
    sleeps = []

    class FakeClient:
        async def get(self, *args, **kwargs):
            return responses.pop(0)

        async def aclose(self):
            pass

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.client.asyncio.sleep", fake_sleep)
    async with DataGovClient(client=FakeClient()) as client:
        await client.fetch_all("psi", "2026-10-15")
    assert sleeps == [1]


@pytest.mark.asyncio
async def test_client_accepts_real_no_data_response():
    class FakeClient:
        async def get(self, *args, **kwargs):
            return httpx.Response(404, json=load("empty-2026-12-31.json"))

        async def aclose(self):
            pass

    async with DataGovClient(client=FakeClient()) as client:
        payload = await client.fetch_all("rainfall", "2026-12-31")
    assert payload["code"] == 17
    assert payload["data"] is None


@pytest.mark.asyncio
async def test_client_raises_after_429_retries(monkeypatch):
    class FakeClient:
        async def get(self, *args, **kwargs):
            return httpx.Response(429, json={"code": 24})

        async def aclose(self):
            pass

    async with DataGovClient(client=FakeClient(), max_retries=0) as client:
        with pytest.raises(DataGovError):
            await client.fetch_all("psi", "2026-10-15")
