from datetime import datetime, timezone

import pytest

from src.exceptions import JobNotFoundError
from src.job_store import InMemoryJobStore
from src.models import JobRecord, JobStatus


def _job(job_id: str = "job-1") -> JobRecord:
    return JobRecord(job_id=job_id, file_name="dwg.pdf", status=JobStatus.PROCESSING, created_at=datetime.now(timezone.utc))


def test_create_then_get_round_trips():
    store = InMemoryJobStore()
    job = _job()

    store.create(job)

    assert store.get("job-1") == job


def test_get_unknown_job_raises_not_found():
    store = InMemoryJobStore()
    with pytest.raises(JobNotFoundError):
        store.get("does-not-exist")


def test_update_unknown_job_raises_not_found():
    store = InMemoryJobStore()
    with pytest.raises(JobNotFoundError):
        store.update(_job())


def test_update_persists_status_change():
    store = InMemoryJobStore()
    job = _job()
    store.create(job)

    job.status = JobStatus.COMPLETE
    store.update(job)

    assert store.get("job-1").status == JobStatus.COMPLETE
