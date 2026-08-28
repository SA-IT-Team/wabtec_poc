"""Persistence for ReconciliationRecord: one JSON blob per job, in its own `reconciliation`
container. A single-blob-per-job design (rather than one Table Storage row per balloon) mirrors
this POC's existing "keep it to one artifact per concern" pattern (compare excel_writer.py's one
workbook per job) -- fine at POC volume, and it means the whole record round-trips through
pydantic with no separate schema to keep in sync. architecture-full.md's production design uses a
proper relational store (Postgres) instead, once query needs (e.g. "all open balloons across a
program") grow past what a per-job blob can answer.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod

from azure.storage.blob import BlobServiceClient

from src.exceptions import JobNotFoundError
from src.models import ReconciliationRecord
from src.storage_helpers import get_container, read_blob_bytes

RECONCILIATION_CONTAINER = "reconciliation"


class IReconciliationStore(ABC):
    @abstractmethod
    def save(self, record: ReconciliationRecord) -> None: ...

    @abstractmethod
    def load(self, job_id: str) -> ReconciliationRecord:
        """Raises JobNotFoundError if no record exists for this job id."""

    @abstractmethod
    def exists(self, job_id: str) -> bool: ...


class InMemoryReconciliationStore(IReconciliationStore):
    """Used by tests and by a local dry-run without Blob Storage configured."""

    def __init__(self):
        self._records: dict[str, ReconciliationRecord] = {}

    def save(self, record: ReconciliationRecord) -> None:
        self._records[record.job_id] = record

    def load(self, job_id: str) -> ReconciliationRecord:
        try:
            return self._records[job_id]
        except KeyError as exc:
            raise JobNotFoundError(job_id) from exc

    def exists(self, job_id: str) -> bool:
        return job_id in self._records


class BlobReconciliationStore(IReconciliationStore):
    """Real implementation: one JSON blob per job at `reconciliation/{job_id}.json`."""

    def __init__(self, connection_string: str):
        self._blob_service = BlobServiceClient.from_connection_string(connection_string)

    def save(self, record: ReconciliationRecord) -> None:
        blob_name = f"{record.job_id}.json"
        get_container(self._blob_service, RECONCILIATION_CONTAINER).upload_blob(
            blob_name, record.model_dump_json().encode("utf-8"), overwrite=True
        )

    def load(self, job_id: str) -> ReconciliationRecord:
        try:
            raw = read_blob_bytes(self._blob_service, RECONCILIATION_CONTAINER, f"{job_id}.json")
        except Exception as exc:  # noqa: BLE001 - the Azure SDK's ResourceNotFoundError, normalized here
            raise JobNotFoundError(job_id) from exc
        return ReconciliationRecord.model_validate(json.loads(raw))

    def exists(self, job_id: str) -> bool:
        try:
            self.load(job_id)
            return True
        except JobNotFoundError:
            return False
