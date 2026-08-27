"""UploadHandler: validates the incoming file, stores it in Blob Storage, and creates the job
record (architecture-poc.md §2.1, requirements FR-01..FR-03).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable, Protocol

from src.exceptions import ValidationError
from src.job_store import IJobStore
from src.models import JobRecord, JobStatus
from src.preprocessor import SUPPORTED_IMAGE_TYPES

SUPPORTED_CONTENT_TYPES = SUPPORTED_IMAGE_TYPES | {"application/pdf"}
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024


class BlobContainer(Protocol):
    def upload_blob(self, blob_name: str, data: bytes, overwrite: bool = True) -> None: ...


BlobContainerFactory = Callable[[str], BlobContainer]


class UploadHandler:
    def __init__(self, blob_container_factory: BlobContainerFactory, job_store: IJobStore):
        self._blob_container_factory = blob_container_factory
        self._job_store = job_store

    def handle_upload(self, *, file_bytes: bytes, file_name: str, content_type: str) -> JobRecord:
        self._validate(file_bytes, content_type)
        job_id = str(uuid.uuid4())
        self._store_source_blob(job_id, file_bytes, file_name)

        job = JobRecord(
            job_id=job_id,
            file_name=file_name,
            status=JobStatus.PROCESSING,
            created_at=datetime.now(timezone.utc),
        )
        self._job_store.create(job)
        return job

    @staticmethod
    def _validate(file_bytes: bytes, content_type: str) -> None:
        if content_type not in SUPPORTED_CONTENT_TYPES:
            raise ValidationError(
                f"Unsupported content type '{content_type}'. Supported: application/pdf, "
                f"{', '.join(sorted(SUPPORTED_IMAGE_TYPES))}."
            )
        if not file_bytes:
            raise ValidationError("Uploaded file is empty.")
        if len(file_bytes) > MAX_FILE_SIZE_BYTES:
            raise ValidationError(f"File exceeds the maximum size of {MAX_FILE_SIZE_BYTES} bytes.")

    def _store_source_blob(self, job_id: str, file_bytes: bytes, file_name: str) -> None:
        container = self._blob_container_factory("raw-drawings")
        blob_name = f"{job_id}/source_{file_name}"
        container.upload_blob(blob_name, file_bytes, overwrite=True)
