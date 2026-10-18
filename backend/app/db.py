from collections.abc import Generator
from pathlib import Path
import json
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    path = Path(os.getenv("DB_PATH", "./weather.db"))
    return f"sqlite:///{path.resolve().as_posix()}"


engine = create_engine(database_url(), connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def create_tables() -> None:
    from . import models

    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate() -> None:
    """Add columns introduced after a database was first created (create_all never alters tables)."""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(readings)")}
        if "partial" in columns:
            return
        conn.exec_driver_sql("ALTER TABLE readings ADD COLUMN partial BOOLEAN NOT NULL DEFAULT 0")
        rows = conn.exec_driver_sql("SELECT date, rain_reading_count, raw_json FROM readings WHERE fetched_at IS NOT NULL").fetchall()
        for day, count, raw in rows:
            try:
                stations = len(json.loads(raw)["rainfall"]["stations"]) or 1
            except (TypeError, ValueError, KeyError):
                stations = 1
            if count < 288 * stations * 0.9:
                conn.exec_driver_sql("UPDATE readings SET partial = 1 WHERE date = ?", (day,))
