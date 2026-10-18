from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator


class ReadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    site: str
    rain_24h_mm: float | None
    rain_48h_mm: float | None
    temp_c: float | None
    psi_24h: float | None
    pm25: float | None
    haze: str | None
    rain_after_haze: str | None
    sampling_notes: str | None
    fetched_at: datetime | None
    updated_at: datetime
    raw_json: str | None
    rain_reading_count: int
    temp_reading_count: int
    psi_reading_count: int
    pm25_reading_count: int
    partial: bool


class ManualReadingUpdate(BaseModel):
    haze: str | None = None
    rain_after_haze: str | None = None
    sampling_notes: str | None = None

    @field_validator("haze", "rain_after_haze")
    @classmethod
    def validate_flag(cls, value: str | None) -> str | None:
        if value is not None and value not in {"Y", "N"}:
            raise ValueError("value must be Y, N, or null")
        return value

    @field_validator("sampling_notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 4000:
            raise ValueError("sampling_notes must be at most 4000 characters")
        return value


class FetchStatus(BaseModel):
    date: date
    status: str
    message: str | None = None
    partial: bool = False


class StatsResponse(BaseModel):
    days_with_data: int
    total_rainfall_mm: float | None
    wettest_day: date | None
    wettest_day_rainfall_mm: float | None
    average_psi: float | None
    average_pm25: float | None
    haze_days_with_rain_after: int
    basis_days: dict[str, int]


class JobStatus(BaseModel):
    id: str
    kind: str
    status: str
    total: int
    completed: int
    results: list[FetchStatus]
    error: str | None = None
