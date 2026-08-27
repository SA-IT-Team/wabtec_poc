import pytest

from src.exceptions import ValidationError
from src.job_store import InMemoryJobStore
from src.models import JobStatus
from src.upload_handler import UploadHandler


class _RecordingContainer:
    def __init__(self):
        self.uploads = []

    def upload_blob(self, blob_name, data, overwrite=True):
        self.uploads.append((blob_name, data))


def _handler():
    container = _RecordingContainer()
    job_store = InMemoryJobStore()
    handler = UploadHandler(lambda name: container, job_store)
    return handler, container, job_store


def test_valid_upload_creates_job_and_stores_blob():
    handler, container, job_store = _handler()

    job = handler.handle_upload(file_bytes=b"%PDF-1.4 ...", file_name="dwg.pdf", content_type="application/pdf")

    assert job.status == JobStatus.PROCESSING
    assert job_store.get(job.job_id) == job
    assert len(container.uploads) == 1
    assert container.uploads[0][0] == f"{job.job_id}/source_dwg.pdf"


def test_rejects_unsupported_content_type():
    handler, _, _ = _handler()
    with pytest.raises(ValidationError):
        handler.handle_upload(file_bytes=b"data", file_name="dwg.zip", content_type="application/zip")


def test_rejects_empty_file():
    handler, _, _ = _handler()
    with pytest.raises(ValidationError):
        handler.handle_upload(file_bytes=b"", file_name="dwg.pdf", content_type="application/pdf")


def test_rejects_oversized_file():
    handler, _, _ = _handler()
    from src.upload_handler import MAX_FILE_SIZE_BYTES

    oversized = b"0" * (MAX_FILE_SIZE_BYTES + 1)
    with pytest.raises(ValidationError):
        handler.handle_upload(file_bytes=oversized, file_name="dwg.pdf", content_type="application/pdf")
