"""Find how far back data.gov.sg actually has data:  python -m app.probe

Uses one cheap request per probe (first page only). Set BACKFILL_FROM to the earliest date it reports.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from .client import ENDPOINTS, DataGovClient, DataGovError
from .clock import sg_today


async def has_data(client: DataGovClient, endpoint: str, day: date) -> bool:
    try:
        payload = await client.fetch_page(endpoint, day.isoformat())
    except DataGovError:
        return False
    return payload.get("code") != 17 and bool(payload.get("data"))


async def earliest(client: DataGovClient, endpoint: str, newest: date, max_days: int = 3650) -> date | None:
    """Assumes data is contiguous from some start date up to `newest`."""
    if not await has_data(client, endpoint, newest):
        return None
    good, step = 0, 1
    while step <= max_days and await has_data(client, endpoint, newest - timedelta(days=step)):
        good, step = step, step * 2
    low, high = good, min(step, max_days + 1)  # low has data; high does not (or is past the limit)
    while high - low > 1:
        mid = (low + high) // 2
        if await has_data(client, endpoint, newest - timedelta(days=mid)):
            low = mid
        else:
            high = mid
    return newest - timedelta(days=low)


async def main() -> None:
    newest = sg_today() - timedelta(days=1)
    async with DataGovClient() as client:
        for endpoint in ENDPOINTS:
            found = await earliest(client, endpoint, newest)
            print(f"{endpoint:16} earliest date with data: {found or 'none found'}")


if __name__ == "__main__":
    asyncio.run(main())
