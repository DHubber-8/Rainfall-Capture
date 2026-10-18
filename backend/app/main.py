from datetime import date, datetime, timezone
import asyncio
import os

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import create_tables, get_db
from .fetching import fetch_range
from .models import Reading
from .scheduler import backfill_missing, create_scheduler
from .schemas import FetchStatus, ManualReadingUpdate, ReadingResponse, StatsResponse


app = FastAPI(title="Woodlands Rain & Haze Dashboard")
scheduler = None
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


@app.on_event("startup")
def startup() -> None:
    global scheduler
    create_tables()
    if os.getenv("ENABLE_SCHEDULER", "1").lower() in {"1", "true", "yes"}:
        scheduler = create_scheduler()
        scheduler.start()
    if os.getenv("BACKFILL_ON_STARTUP", "0").lower() in {"1", "true", "yes"}:
        asyncio.create_task(backfill_missing())


@app.on_event("shutdown")
def shutdown() -> None:
    if scheduler is not None:
        scheduler.shutdown(wait=False)


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


@app.post("/api/fetch", response_model=list[FetchStatus])
async def fetch_readings(
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    db: Session = Depends(get_db),
) -> list[FetchStatus]:
    try:
        results = await fetch_range(db, from_date, to_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [FetchStatus.model_validate(result, from_attributes=True) for result in results]


@app.post("/api/backfill", response_model=list[FetchStatus])
async def backfill_readings() -> list[FetchStatus]:
    results = await backfill_missing()
    return [FetchStatus.model_validate(result, from_attributes=True) for result in results]
