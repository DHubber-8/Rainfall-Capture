from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Reading(Base):
    __tablename__ = "readings"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    site: Mapped[str] = mapped_column(String(64), default="Woodlands", nullable=False)
    rain_24h_mm: Mapped[float | None] = mapped_column(Float)
    rain_48h_mm: Mapped[float | None] = mapped_column(Float)
    temp_c: Mapped[float | None] = mapped_column(Float)
    psi_24h: Mapped[float | None] = mapped_column(Float)
    pm25: Mapped[float | None] = mapped_column(Float)
    haze: Mapped[str | None] = mapped_column(String(1))
    rain_after_haze: Mapped[str | None] = mapped_column(String(1))
    sampling_notes: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    raw_json: Mapped[str | None] = mapped_column(Text)
    rain_reading_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    temp_reading_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    psi_reading_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pm25_reading_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class FetchAttempt(Base):
    """Last fetch outcome per date, so no-data and failed days are remembered instead of retried blindly."""

    __tablename__ = "fetch_attempts"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
