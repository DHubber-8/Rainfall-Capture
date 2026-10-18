# Rainfall-Capture — Woodlands rain & haze dashboard

Collects daily rainfall, temperature, PSI and PM2.5 for Woodlands from the data.gov.sg real-time API,
stores them in SQLite and shows them in a React dashboard. Haze, rain-after-haze and notes are entered by hand.
No data is seeded or estimated: a day with nothing from the API is a gap (NULL), never 0.

## Run
```bash
cd backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[test]"
cp .env.example .env                                  # add DATA_GOV_SG_API_KEY if you have one
python -m app.probe                                   # how far back does the API have data? set BACKFILL_FROM
uvicorn app.main:app --reload                         # http://localhost:8000
cd ../frontend && npm install && npm run dev          # http://localhost:5173
cd ../backend && pytest
```

## How fetching works
- **Rainfall**: the API gives 5-minute totals, so every page of the day is read (~12 requests) and summed.
  Sampling it hourly would keep only 1 of every 12 totals and under-report rain ~12x, so this is deliberately not sampled.
- **Temperature**: 1-minute readings, averaged, so it is sampled every `TEMP_SAMPLE_MINUTES` (30) with date-time queries.
- **PSI / PM2.5**: one request each (north region).
- A fetch of N days is a background job (`POST /api/fetch` → `GET /api/jobs/{id}`); only one runs at a time.
- Requests are throttled, retried on 429/5xx/timeouts (honouring `Retry-After`), and one failing day never aborts a range.
- Backfill (startup, button, nightly) fetches days that are missing, only partially fetched, or only hold manual notes,
  and remembers days the API had no data for.
- 48h rainfall = that day + the previous day, only when both days are complete; otherwise NULL.
