"""In-process background fetch jobs, so long fetches never block (and time out) an HTTP request."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import logging
from typing import Callable
from uuid import uuid4

from .client import DataGovClient
from .db import SessionLocal
from .fetching import FetchResult, fetch_dates

logger = logging.getLogger(__name__)
MAX_JOBS_KEPT = 20


@dataclass
class Job:
    id: str
    kind: str
    total: int
    status: str = "running"
    completed: int = 0
    results: list[FetchResult] = field(default_factory=list)
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


class JobBusy(RuntimeError):
    def __init__(self, job: Job) -> None:
        super().__init__(f"A fetch is already running (job {job.id}, {job.completed}/{job.total} days done)")
        self.job = job


JOBS: dict[str, Job] = {}
_tasks: set[asyncio.Task] = set()


def current_job() -> Job | None:
    return next((job for job in JOBS.values() if job.status == "running"), None)


def get_job(job_id: str) -> Job | None:
    return JOBS.get(job_id)


async def _run(job: Job, dates: list[date], client_factory: Callable[[], DataGovClient] | None) -> None:
    def progress(result: FetchResult) -> None:
        job.results.append(result)
        job.completed += 1

    try:
        with SessionLocal() as db:
            await fetch_dates(db, dates, client_factory, on_progress=progress)
        job.status = "done"
    except Exception as exc:  # fetch_dates isolates per-date errors; this is a last resort
        logger.exception("fetch job %s failed", job.id)
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.finished_at = datetime.now(timezone.utc)


def start_job(kind: str, dates: list[date], client_factory: Callable[[], DataGovClient] | None = None) -> Job:
    running = current_job()
    if running is not None:
        raise JobBusy(running)
    job = Job(id=uuid4().hex[:12], kind=kind, total=len(dates))
    JOBS[job.id] = job
    for old in sorted(JOBS.values(), key=lambda item: item.started_at)[:-MAX_JOBS_KEPT]:
        if old.status != "running":
            JOBS.pop(old.id, None)
    if not dates:
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        return job
    task = asyncio.create_task(_run(job, dates, client_factory))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job
