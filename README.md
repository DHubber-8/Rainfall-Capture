# Rainfall-Capture — Woodlands rain & haze dashboard

Collects daily rainfall, temperature, PSI and PM2.5 for Woodlands from the data.gov.sg real-time API,
stores them in SQLite and shows them in a React dashboard. Haze, rain-after-haze and notes are entered by hand.
No data is seeded or estimated: a day with nothing from the API is a gap (NULL), never 0.

## Run locally

You need a **stable Python 3.11–3.14** installation and **Node 18+**. Do not use a Python 3.15 alpha/interpreter for this project: some dependency wheels may not be compatible with it.

Use two terminals. Keep the backend running in the first terminal while using the frontend in the second.

### 1. Backend (terminal 1)
```bash
cd backend
```

Create the virtual environment with a stable Python version. For example, if Python 3.14 is installed:

```powershell
uv venv --python 3.14 .venv
```

Alternatively, use `python -m venv .venv` when `python --version` reports a stable Python 3.11–3.14 version.

Activate the virtual environment:
- macOS / Linux: `source .venv/bin/activate`
- Windows PowerShell: `.venv\Scripts\Activate.ps1` (if blocked: `Set-ExecutionPolicy -Scope Process RemoteSigned`)
- Windows cmd: `.venv\Scripts\activate.bat`

```bash
pip install -e ".[test]"
```

*Optional*
If `.venv` was previously created with Python 3.15 alpha, remove that environment and recreate it with a stable interpreter before installing dependencies:

```powershell
Remove-Item -Recurse -Force .venv
uv venv --python 3.14 .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
```

Create your settings file:
- macOS / Linux: `cp .env.example .env`
- Windows: `copy .env.example .env`

Open `.env` and set `DATA_GOV_SG_API_KEY=` if you have a key (optional; leave it empty otherwise).

Find out how far back the API has data, then put that date in `BACKFILL_FROM` in `.env`:
```bash
python -m app.probe
```
Start the server (leave it running):
```bash
uvicorn app.main:app --reload
```
Leave this terminal running. Check that the backend started by opening http://localhost:8000/docs. On first start it creates `weather.db` and backfills missing days in the background.

### 2. Frontend (terminal 2)
```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 . If the backend is not on port 8000, create `frontend/.env` with `VITE_API_URL=http://localhost:PORT` and restart `npm run dev`.

### 3. Tests (any terminal, backend venv active)
```bash
cd backend
pytest
```

### Troubleshooting
- **The backend exits with `NameError: name 'sentinel' is not defined` while importing `anyio`:** the virtual environment is using Python 3.15 alpha or incompatible dependency wheels. Recreate `.venv` with stable Python 3.11–3.14 as shown above.
- **The frontend says `Failed to fetch` or shows a refused connection:** confirm the backend terminal is still running and that http://localhost:8000/docs opens. If the backend uses another port, set `VITE_API_URL` in `frontend/.env` and restart the frontend.
- **Page loads but shows a network or CORS error:** the backend is not running, or `CORS_ORIGINS` in `.env` doesn't include the address in your browser bar. Restart uvicorn after editing `.env`.
- **Fetch shows `rate limit persisted` errors:** add an API key, or raise `DATA_GOV_REQUEST_INTERVAL` (for example `0.5`).
- **A day shows `no-data`:** the API has nothing for that date. Run `python -m app.probe` to see its earliest date. These days are retried after `NO_DATA_RETRY_DAYS`.
- **Start fresh:** stop the backend and delete `backend/weather.db`.

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
