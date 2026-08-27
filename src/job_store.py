"""JobStore: thin wrapper over Azure Table Storage (architecture-poc.md §4.1), plus an in-memory
implementation used by tests and local dry-runs.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from src.exceptions import JobNotFoundError
from src.models import JobRecord, JobStatus


class IJobStore(ABC):
    @abstractmethod
    def create(self, job: JobRecord) -> None: ...

    @abstractmethod
    def update(self, job: JobRecord) -> None: ...

    @abstractmethod
    def get(self, job_id: str) -> JobRecord: ...


class InMemoryJobStore(IJobStore):
    """Used by tests, and available for a local run without a real Table Storage account."""

    def __init__(self):
        self._jobs: dict[str, JobRecord] = {}

    def create(self, job: JobRecord) -> None:
        self._jobs[job.job_id] = job

    def update(self, job: JobRecord) -> None:
        if job.job_id not in self._jobs:
            raise JobNotFoundError(job.job_id)
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> JobRecord:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise JobNotFoundError(job_id) from exc


class TableStorageJobStore(IJobStore):
    # POC simplification: a single fixed partition. Fine at POC volume (one operator, one job at a
    # time); architecture-full.md's production job/reporting model uses a proper relational store
    # (Postgres) instead of partition-key tricks once query needs grow.
    PARTITION_KEY = "poc"

    def __init__(self, connection_string: str, table_name: str = "jobs"):
        from azure.data.tables import TableServiceClient

        service = TableServiceClient.from_connection_string(connection_string)
        service.create_table_if_not_exists(table_name)
        self._table = service.get_table_client(table_name)

    def create(self, job: JobRecord) -> None:
        self._table.create_entity(self._to_entity(job))

    def update(self, job: JobRecord) -> None:
        self._table.upsert_entity(self._to_entity(job))

    def get(self, job_id: str) -> JobRecord:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            entity = self._table.get_entity(partition_key=self.PARTITION_KEY, row_key=job_id)
        except ResourceNotFoundError as exc:
            raise JobNotFoundError(job_id) from exc
        return self._from_entity(entity)

    def _to_entity(self, job: JobRecord) -> dict:
        return {
            "PartitionKey": self.PARTITION_KEY,
            "RowKey": job.job_id,
            "fileName": job.file_name,
            "status": job.status.value,
            "drawingNumber": job.drawing_number or "",
            "revision": job.revision or "",
            "balloonCountDetected": job.balloon_count_detected,
            "balloonCountExtracted": job.balloon_count_extracted,
            "avgConfidence": job.avg_confidence if job.avg_confidence is not None else 0.0,
            "createdAt": job.created_at.isoformat(),
            "completedAt": job.completed_at.isoformat() if job.completed_at else "",
            "errorReason": job.error_reason or "",
        }

    @staticmethod
    def _from_entity(entity: dict) -> JobRecord:
        return JobRecord(
            job_id=entity["RowKey"],
            file_name=entity["fileName"],
            status=JobStatus(entity["status"]),
            drawing_number=entity.get("drawingNumber") or None,
            revision=entity.get("revision") or None,
            balloon_count_detected=entity.get("balloonCountDetected", 0),
            balloon_count_extracted=entity.get("balloonCountExtracted", 0),
            avg_confidence=entity.get("avgConfidence") or None,
            created_at=datetime.fromisoformat(entity["createdAt"]),
            completed_at=datetime.fromisoformat(entity["completedAt"]) if entity.get("completedAt") else None,
            error_reason=entity.get("errorReason") or None,
        )
