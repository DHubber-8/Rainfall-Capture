from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import Reading


def make_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), engine


def test_fresh_database_has_zero_rows(tmp_path):
    client, engine = make_client(tmp_path)
    assert client.get("/api/readings").json() == []
    with session_factory_for(engine)() as session:
        assert session.scalar(select(func.count()).select_from(Reading)) == 0
    app.dependency_overrides.clear()


def test_patch_creates_only_manual_fields(tmp_path):
    client, engine = make_client(tmp_path)
    response = client.patch(
        f"/api/readings/{date(2026, 1, 1).isoformat()}",
        json={"haze": "Y", "rain_after_haze": "N", "sampling_notes": "Observed haze"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["haze"] == "Y"
    assert body["rain_after_haze"] == "N"
    assert body["rain_24h_mm"] is None
    assert body["rain_reading_count"] == 0
    app.dependency_overrides.clear()


def test_patch_rejects_invalid_manual_flag(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.patch("/api/readings/2026-01-01", json={"haze": "maybe"})
    assert response.status_code == 422
    app.dependency_overrides.clear()


def test_stats_uses_only_non_null_metrics(tmp_path):
    client, engine = make_client(tmp_path)
    with session_factory_for(engine)() as session:
        session.add_all(
            [
                Reading(
                    date=date(2026, 1, 1),
                    rain_24h_mm=10,
                    psi_24h=80,
                    pm25=None,
                    haze="Y",
                    rain_after_haze="Y",
                ),
                Reading(date=date(2026, 1, 2), rain_24h_mm=None, psi_24h=100, pm25=20),
            ]
        )
        session.commit()
    response = client.get("/api/stats?from=2026-01-01&to=2026-01-02")
    assert response.status_code == 200
    assert response.json() == {
        "days_with_data": 1,
        "total_rainfall_mm": 10.0,
        "wettest_day": "2026-01-01",
        "wettest_day_rainfall_mm": 10.0,
        "average_psi": 90.0,
        "average_pm25": 20.0,
        "haze_days_with_rain_after": 1,
        "basis_days": {"rainfall": 1, "psi": 2, "pm25": 1, "haze": 1},
    }
    app.dependency_overrides.clear()


def session_factory_for(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
