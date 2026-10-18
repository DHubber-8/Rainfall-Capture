from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx


BASE_URL = "https://api-open.data.gov.sg/v2/real-time/api"
ENDPOINTS = ("rainfall", "air-temperature", "psi", "pm25")


class DataGovError(RuntimeError):
    pass


class DataGovClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        max_retries: int = 4,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("DATA_GOV_SG_API_KEY")
        self.max_retries = max_retries
        self._client = client

    async def __aenter__(self) -> DataGovClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_page(
        self, endpoint: str, date: str, pagination_token: str | None = None
    ) -> dict[str, Any]:
        if endpoint not in ENDPOINTS:
            raise ValueError(f"unsupported data.gov.sg endpoint: {endpoint}")
        if self._client is None:
            raise RuntimeError("DataGovClient must be used as an async context manager")

        params: dict[str, str] = {"date": date}
        if pagination_token:
            params["paginationToken"] = pagination_token
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        for attempt in range(self.max_retries + 1):
            response = await self._client.get(f"{self.base_url}/{endpoint}", params=params, headers=headers)
            if response.status_code == 429:
                if attempt == self.max_retries:
                    raise DataGovError(f"{endpoint} rate limit persisted after retries")
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code >= 400:
                payload = response.json()
                if payload.get("code") == 17:
                    return payload
                raise DataGovError(f"{endpoint} returned HTTP {response.status_code}")
            payload = response.json()
            if payload.get("code") not in (None, 0, 17):
                raise DataGovError(payload.get("errorMsg") or f"{endpoint} returned API code {payload['code']}")
            return payload
        raise AssertionError("unreachable")

    async def fetch_all(self, endpoint: str, date: str) -> dict[str, Any]:
        first = await self.fetch_page(endpoint, date)
        if endpoint in {"psi", "pm25"}:
            return first

        if first.get("data") is None:
            return first
        pages = [first]
        token = first.get("data", {}).get("paginationToken")
        while token:
            page = await self.fetch_page(endpoint, date, token)
            pages.append(page)
            token = page.get("data", {}).get("paginationToken")

        merged = dict(first)
        merged_data = dict(first.get("data") or {})
        merged_data["readings"] = [
            reading
            for page in pages
            for reading in (page.get("data") or {}).get("readings", [])
        ]
        merged_data.pop("paginationToken", None)
        merged["data"] = merged_data
        return merged
