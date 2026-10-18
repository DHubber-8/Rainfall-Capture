from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import os
import time
from typing import Any

import httpx


BASE_URL = "https://api-open.data.gov.sg/v2/real-time/api"
ENDPOINTS = ("rainfall", "air-temperature", "psi", "pm25")
LIST_KEYS = ("readings", "items")
NO_DATA = {"code": 17, "data": None, "errorMsg": "Data not found"}


class DataGovError(RuntimeError):
    pass


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


class DataGovClient:
    """Polite data.gov.sg client: throttled, retried, and never raises anything but DataGovError for API trouble."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        max_retries: int = 5,
        client: httpx.AsyncClient | None = None,
        request_interval: float | None = None,
        concurrency: int | None = None,
        max_pages: int = 400,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("DATA_GOV_SG_API_KEY")
        self.max_retries = max_retries
        self.request_interval = (
            request_interval if request_interval is not None else _env_number("DATA_GOV_REQUEST_INTERVAL", 0.25)
        )
        self.concurrency = max(1, int(concurrency if concurrency is not None else _env_number("DATA_GOV_CONCURRENCY", 3)))
        self.max_pages = max_pages
        self._client = client
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def __aenter__(self) -> DataGovClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---- low level -------------------------------------------------------------------------

    async def _throttle(self) -> None:
        if self.request_interval <= 0:
            return
        async with self._lock:
            wait = self._last_request + self.request_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After") if response is not None else None
        try:
            return min(float(retry_after), 120.0)
        except (TypeError, ValueError):
            return float(min(2**attempt, 60))

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    async def _request(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        if endpoint not in ENDPOINTS:
            raise ValueError(f"unsupported data.gov.sg endpoint: {endpoint}")
        if self._client is None:
            raise RuntimeError("DataGovClient must be used as an async context manager")

        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        url = f"{self.base_url}/{endpoint}"

        for attempt in range(self.max_retries + 1):
            await self._throttle()
            try:
                response = await self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:  # timeouts, connection resets, DNS, ...
                if attempt == self.max_retries:
                    raise DataGovError(f"{endpoint} network error ({type(exc).__name__}) after {attempt + 1} attempts") from exc
                await asyncio.sleep(min(2**attempt, 60))
                continue

            status = response.status_code
            if status == 429 or status >= 500:
                if attempt == self.max_retries:
                    what = "rate limit" if status == 429 else f"server error {status}"
                    raise DataGovError(f"{endpoint} {what} persisted after retries")
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue

            payload = self._json(response)
            if status >= 400:
                if isinstance(payload, dict) and (payload.get("code") == 17 or (status == 404 and not payload.get("data"))):
                    return {**NO_DATA, **payload, "data": None}
                detail = payload.get("errorMsg") if isinstance(payload, dict) else None
                raise DataGovError(f"{endpoint} returned HTTP {status}" + (f": {detail}" if detail else ""))
            if not isinstance(payload, dict):
                raise DataGovError(f"{endpoint} returned a non-JSON response")
            if payload.get("code") not in (None, 0, 17):
                raise DataGovError(payload.get("errorMsg") or f"{endpoint} returned API code {payload['code']}")
            return payload
        raise AssertionError("unreachable")

    # ---- public ---------------------------------------------------------------------------

    async def fetch_page(
        self, endpoint: str, date: str, pagination_token: str | None = None
    ) -> dict[str, Any]:
        """One request. `date` is YYYY-MM-DD (whole day, paginated) or YYYY-MM-DDTHH:MM:SS (latest reading at that moment)."""
        params = {"date": date}
        if pagination_token:
            params["paginationToken"] = pagination_token
        return await self._request(endpoint, params)

    async def fetch_all(self, endpoint: str, date: str) -> dict[str, Any]:
        """Every page for one SGT day, merged into a single payload."""
        first = await self.fetch_page(endpoint, date)
        data = first.get("data")
        if not data:
            return first

        pages = [first]
        seen: set[str] = set()
        token = data.get("paginationToken")
        while token:
            if token in seen or len(pages) >= self.max_pages:
                raise DataGovError(f"{endpoint} pagination did not terminate")
            seen.add(token)
            page = await self.fetch_page(endpoint, date, token)
            pages.append(page)
            token = (page.get("data") or {}).get("paginationToken")

        merged = dict(first)
        merged_data = dict(data)
        for key in LIST_KEYS:
            if key in data:
                merged_data[key] = [row for page in pages for row in (page.get("data") or {}).get(key, [])]
        merged_data.pop("paginationToken", None)
        merged["data"] = merged_data
        return merged

    async def fetch_snapshots(
        self, endpoint: str, target: date, step_minutes: int, until: datetime | None = None
    ) -> dict[str, Any]:
        """Sample one SGT day every `step_minutes` using date-time queries (one request per sample, no paging).

        Use for readings that are averaged (temperature). Never use it for rainfall: those are 5-minute
        totals, so sampling them would drop most of the rain.
        """
        step = max(1, int(step_minutes))
        moment = datetime.combine(target, datetime.min.time())
        end = moment + timedelta(days=1)
        limit = until.replace(tzinfo=None) if until is not None else None
        stamps: list[datetime] = []
        while moment < end:
            if limit is None or moment <= limit:
                stamps.append(moment)
            moment += timedelta(minutes=step)

        semaphore = asyncio.Semaphore(self.concurrency)

        async def one(stamp: datetime) -> dict[str, Any]:
            async with semaphore:
                return await self.fetch_page(endpoint, stamp.strftime("%Y-%m-%dT%H:%M:%S"))

        results = await asyncio.gather(*(one(stamp) for stamp in stamps), return_exceptions=True)
        failures = [item for item in results if isinstance(item, BaseException)]
        for failure in failures:
            if not isinstance(failure, DataGovError):
                raise failure
        good = [item for item in results if isinstance(item, dict) and item.get("data")]
        if not good:
            if failures:
                raise failures[0]
            return dict(NO_DATA)

        seen: set[str] = set()
        readings: list[dict[str, Any]] = []
        for payload in good:
            for reading in payload["data"].get("readings", []):
                stamp = reading.get("timestamp")
                if stamp in seen:
                    continue
                seen.add(stamp)
                readings.append(reading)

        merged = dict(good[0])
        merged_data = dict(good[0]["data"])
        merged_data["readings"] = readings
        merged_data.pop("paginationToken", None)
        merged["data"] = merged_data
        merged["sample_failures"] = len(failures)
        return merged
