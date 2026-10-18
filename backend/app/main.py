from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
import asyncio
import os

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import create_tables, get_db
from .fetching import date_span
from .jobs import Job, JobBusy, current_job, get_job, start_job
from .models import Reading
from .scheduler import create_scheduler, missing_dates, startup_backfill
from .schemas import FetchStatus, JobStatus, ManualReadingUpdate, ReadingResponse, StatsResponse

scheduler = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global scheduler
    create_tables()
    if os.getenv("ENABLE_SCHEDULER", "1").lower() in {"1", "true", "yes"}:
        scheduler = create_scheduler()
        scheduler.start()
    if os.getenv("BACKFILL_ON_STARTUP", "1").lower() in {"1", "true", "yes"}:
        asyncio.create_task(startup_backfill())
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Woodlands Rain & Haze Dashboard", lifespan=lifespan)
origins = [
    item.strip()
    for item in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/readings", response_model=list[ReadingResponse])
def list_readings(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
) -> list[Reading]:
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=400, detail="from must not be after to")
    statement = select(Reading).order_by(Reading.date)
    if from_date:
        statement = statement.where(Reading.date >= from_date)
    if to_date:
        statement = statement.where(Reading.date <= to_date)
    return list(db.scalars(statement))


@app.get("/api/stats", response_model=StatsResponse)
def stats(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
) -> StatsResponse:
    statement = select(Reading).order_by(Reading.date)
    if from_date:
        statement = statement.where(Reading.date >= from_date)
    if to_date:
        statement = statement.where(Reading.date <= to_date)
    readings = list(db.scalars(statement))
    with_rain = [item for item in readings if item.rain_24h_mm is not None]
    with_psi = [item for item in readings if item.psi_24h is not None]
    with_pm25 = [item for item in readings if item.pm25 is not None]
    haze_rain = [
        item for item in readings
        if item.haze == "Y" and item.rain_after_haze == "Y"
    ]
    wettest = max(with_rain, key=lambda item: item.rain_24h_mm) if with_rain else None
    return StatsResponse(
        days_with_data=len(with_rain),
        total_rainfall_mm=sum(item.rain_24h_mm for item in with_rain) if with_rain else None,
        wettest_day=wettest.date if wettest else None,
        wettest_day_rainfall_mm=wettest.rain_24h_mm if wettest else None,
        average_psi=sum(item.psi_24h for item in with_psi) / len(with_psi) if with_psi else None,
        average_pm25=sum(item.pm25 for item in with_pm25) / len(with_pm25) if with_pm25 else None,
        haze_days_with_rain_after=len(haze_rain),
        basis_days={
            "rainfall": len(with_rain),
            "psi": len(with_psi),
            "pm25": len(with_pm25),
            "haze": len(haze_rain),
        },
    )


@app.patch("/api/readings/{reading_date}", response_model=ReadingResponse)
def update_manual_fields(
    reading_date: date,
    update: ManualReadingUpdate,
    db: Session = Depends(get_db),
) -> Reading:
    reading = db.get(Reading, reading_date)
    if reading is None:
        reading = Reading(date=reading_date, updated_at=datetime.now(timezone.utc))
        db.add(reading)
    reading.haze = update.haze
    reading.rain_after_haze = update.rain_after_haze
    reading.sampling_notes = update.sampling_notes
    reading.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(reading)
    return reading


def _job_status(job: Job) -> JobStatus:
    return JobStatus(
        id=job.id,
        kind=job.kind,
        status=job.status,
        total=job.total,
        completed=job.completed,
        results=[FetchStatus.model_validate(item, from_attributes=True) for item in job.results],
        error=job.error,
    )


def _start(kind: str, dates: list[date]) -> JobStatus:
    try:
        job = start_job(kind, dates)
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_status(job)


@app.post("/api/fetch", response_model=JobStatus, status_code=202)
async def fetch_readings(from_date: date = Query(alias="from"), to_date: date = Query(alias="to")):
    """Starts a background fetch and returns a job to poll at /api/jobs/{id}."""
    try:
        dates = date_span(from_date, to_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _start("fetch", dates)


@app.post("/api/backfill", response_model=JobStatus, status_code=202)
async def backfill_readings():
    """Background-fetches every date since BACKFILL_FROM that has no trustworthy stored data."""
    return _start("backfill", missing_dates())


@app.get("/api/jobs/current", response_model=JobStatus | None)
async def running_job() -> JobStatus | None:
    job = current_job()
    return _job_status(job) if job else None


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def job_status(job_id: str) -> JobStatus:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return _job_status(job)
