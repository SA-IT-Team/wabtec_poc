"""Tests for app.py -- the HTTP layer. Covers the API_ACCESS_KEY gate, CORS headers, both upload
paths (direct multipart and blob-first), the reconciliation workflow (review/signoff/export), and
Flask routing/error mapping. The underlying pipeline wiring (build_pipeline, storage helpers,
TableStorageJobStore, build_reconciliation_service) is monkeypatched everywhere here -- it's
already covered by the integration tests, and none of it should touch the network in a unit test.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

import app as vercel_app
from src.exceptions import DocumentIntelligenceError
from src.job_store import InMemoryJobStore
from src.models import ExtractedBalloon, JobRecord, JobStatus
from src.reconciliation import ReconciliationService
from src.reconciliation_store import InMemoryReconciliationStore

ENV = {
    "AZURE_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true",
    "DOCUMENT_INTELLIGENCE_ENDPOINT": "https://fake-di.cognitiveservices.azure.com/",
    "DOCUMENT_INTELLIGENCE_KEY": "fake-di-key",
    "CLAUDE_API_KEY": "fake-claude-key",
    "API_ACCESS_KEY": "test-shared-secret",
}


@pytest.fixture
def client(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    return vercel_app.app.test_client()


def _patch_reconciliation(monkeypatch, store: InMemoryReconciliationStore | None = None) -> InMemoryReconciliationStore:
    """Monkeypatches build_reconciliation_service to always return a ReconciliationService over
    the SAME in-memory store instance -- real business logic, no network, and state survives
    across multiple requests within one test (extract -> review -> signoff -> export)."""
    store = store or InMemoryReconciliationStore()
    monkeypatch.setattr(vercel_app, "build_reconciliation_service", lambda settings: ReconciliationService(store))
    return store


class _FakeContainer:
    def upload_blob(self, blob_name: str, data: bytes, overwrite: bool = True) -> None:
        pass


class _FakePipeline:
    """Stands in for ExtractionPipeline.run: skips real preprocessing/detection/extraction and
    just hands back a context with one canned balloon, matching the shape pipeline.run produces."""

    def run(self, ctx):
        ctx.balloons = [ExtractedBalloon(balloon_number=1, page=1, nominal_value=25.4, confidence=0.9)]
        ctx.balloon_count_detected = 1
        ctx.excel_bytes = b"fake-xlsx-bytes"
        return ctx


class _PrefilledJobStore(InMemoryJobStore):
    def __init__(self, job: JobRecord):
        super().__init__()
        self.create(job)


def test_health_endpoint_is_unauthenticated(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json == {"status": "ok"}


def test_extract_returns_500_when_api_access_key_unconfigured(monkeypatch):
    for key, value in ENV.items():
        if key != "API_ACCESS_KEY":
            monkeypatch.setenv(key, value)
    monkeypatch.delenv("API_ACCESS_KEY", raising=False)
    client = vercel_app.app.test_client()

    resp = client.post("/api/drawings/extract", data={}, headers={"x-api-key": "anything"})

    assert resp.status_code == 500
    assert resp.json["error"] == "ConfigurationError"
    assert "API_ACCESS_KEY" in resp.json["message"]


def test_extract_rejects_missing_api_key(client):
    resp = client.post("/api/drawings/extract", data={})
    assert resp.status_code == 401
    assert resp.json["error"] == "Unauthorized"


def test_extract_rejects_wrong_api_key(client):
    resp = client.post("/api/drawings/extract", data={}, headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


def test_cors_preflight_is_not_gated_by_api_key(client):
    resp = client.options("/api/drawings/extract")
    assert resp.status_code == 204


def test_response_carries_cors_headers(client):
    resp = client.get("/api/health")
    assert resp.headers["Access-Control-Allow-Origin"] == "*"


def test_extract_missing_file_returns_400(client):
    resp = client.post("/api/drawings/extract", data={}, headers={"x-api-key": "test-shared-secret"})
    assert resp.status_code == 400
    assert resp.json["error"] == "ValidationError"


def test_extract_happy_path_returns_200_draft_with_pending_reconciliation(client, monkeypatch):
    fake_job_store = InMemoryJobStore()
    monkeypatch.setattr(vercel_app, "build_pipeline", lambda settings: (_FakePipeline(), fake_job_store, object()))
    monkeypatch.setattr(vercel_app, "get_container", lambda blob_service, name: _FakeContainer())
    _patch_reconciliation(monkeypatch)

    resp = client.post(
        "/api/drawings/extract",
        data={"file": (io.BytesIO(b"%PDF-1.4 fake"), "dwg.pdf", "application/pdf")},
        headers={"x-api-key": "test-shared-secret"},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["balloon_count_detected"] == 1
    assert body["balloon_count_extracted"] == 1
    assert body["balloon_count_mismatch"] is False
    # extraction no longer auto-exports -- nothing is exportable until reconciled + signed off
    assert body["export_url"] is None
    assert body["reconciliation"]["total_balloons"] == 1
    assert body["reconciliation"]["pending"] == 1
    assert body["reconciliation"]["ready_for_signoff"] is False
    assert body["reconciliation"]["signed_off"] is False


def test_extract_maps_document_intelligence_outage_to_502(client, monkeypatch):
    class _FailingPipeline:
        def run(self, ctx):
            raise DocumentIntelligenceError("upstream 503")

    monkeypatch.setattr(vercel_app, "build_pipeline", lambda settings: (_FailingPipeline(), InMemoryJobStore(), object()))
    monkeypatch.setattr(vercel_app, "get_container", lambda blob_service, name: _FakeContainer())

    resp = client.post(
        "/api/drawings/extract",
        data={"file": (io.BytesIO(b"%PDF-1.4 fake"), "dwg.pdf", "application/pdf")},
        headers={"x-api-key": "test-shared-secret"},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 502
    assert resp.json["error"] == "ExtractionServiceError"


def test_get_drawing_result_returns_job_when_found(client, monkeypatch):
    job = JobRecord(job_id="job-1", file_name="dwg.pdf", status=JobStatus.COMPLETE, created_at=datetime.now(timezone.utc))
    monkeypatch.setattr(vercel_app, "TableStorageJobStore", lambda conn_str: _PrefilledJobStore(job))

    resp = client.get("/api/drawings/job-1", headers={"x-api-key": "test-shared-secret"})

    assert resp.status_code == 200
    assert resp.json["job_id"] == "job-1"


def test_get_drawing_result_returns_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(vercel_app, "TableStorageJobStore", lambda conn_str: InMemoryJobStore())

    resp = client.get("/api/drawings/does-not-exist", headers={"x-api-key": "test-shared-secret"})

    assert resp.status_code == 404
    assert resp.json["error"] == "NotFound"


# ---------------------------------------------------------------------------------------
# Large-file path: POST /api/drawings/upload-url + POST /api/drawings/<jobId>/process
# ---------------------------------------------------------------------------------------


def test_create_upload_url_requires_filename_and_content_type(client, monkeypatch):
    monkeypatch.setattr(vercel_app, "BlobServiceClient", _fake_blob_service_client_factory())

    resp = client.post("/api/drawings/upload-url", json={}, headers={"x-api-key": "test-shared-secret"})

    assert resp.status_code == 400
    assert resp.json["error"] == "ValidationError"


def test_create_upload_url_rejects_unsupported_content_type(client, monkeypatch):
    monkeypatch.setattr(vercel_app, "BlobServiceClient", _fake_blob_service_client_factory())

    resp = client.post(
        "/api/drawings/upload-url",
        json={"fileName": "dwg.txt", "contentType": "text/plain"},
        headers={"x-api-key": "test-shared-secret"},
    )

    assert resp.status_code == 400
    assert resp.json["error"] == "UnsupportedFileType"


def test_create_upload_url_returns_sas_and_creates_job(client, monkeypatch):
    monkeypatch.setattr(vercel_app, "BlobServiceClient", _fake_blob_service_client_factory())
    monkeypatch.setattr(
        vercel_app, "generate_upload_sas", lambda blob_service, conn_str, container, blob_name: f"https://fake/{blob_name}?sas=1"
    )
    created_jobs = {}
    monkeypatch.setattr(vercel_app, "TableStorageJobStore", lambda conn_str: _RecordingJobStore(created_jobs))

    resp = client.post(
        "/api/drawings/upload-url",
        json={"fileName": "dwg.pdf", "contentType": "application/pdf"},
        headers={"x-api-key": "test-shared-secret"},
    )

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["blobPath"] == f"{body['jobId']}/source_dwg.pdf"
    assert body["uploadUrl"] == f"https://fake/{body['blobPath']}?sas=1"
    assert body["jobId"] in created_jobs


def test_process_drawing_returns_404_for_unknown_job(client, monkeypatch):
    monkeypatch.setattr(vercel_app, "build_pipeline", lambda settings: (_FakePipeline(), InMemoryJobStore(), object()))

    resp = client.post(
        "/api/drawings/does-not-exist/process",
        json={"blobPath": "x/source_dwg.pdf", "contentType": "application/pdf"},
        headers={"x-api-key": "test-shared-secret"},
    )

    assert resp.status_code == 404
    assert resp.json["error"] == "NotFound"


def test_process_drawing_requires_blob_path_and_content_type(client):
    resp = client.post("/api/drawings/job-1/process", json={}, headers={"x-api-key": "test-shared-secret"})
    assert resp.status_code == 400
    assert resp.json["error"] == "ValidationError"


def test_process_drawing_happy_path_reads_blob_and_returns_draft_result(client, monkeypatch):
    job = JobRecord(job_id="job-1", file_name="dwg.pdf", status=JobStatus.PROCESSING, created_at=datetime.now(timezone.utc))
    fake_job_store = _PrefilledJobStore(job)
    monkeypatch.setattr(vercel_app, "build_pipeline", lambda settings: (_FakePipeline(), fake_job_store, object()))
    monkeypatch.setattr(vercel_app, "read_blob_bytes", lambda blob_service, container, blob_name: b"%PDF-1.4 fake bytes")
    _patch_reconciliation(monkeypatch)

    resp = client.post(
        "/api/drawings/job-1/process",
        json={"blobPath": "job-1/source_dwg.pdf", "contentType": "application/pdf"},
        headers={"x-api-key": "test-shared-secret"},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["job_id"] == "job-1"
    assert body["export_url"] is None
    assert body["reconciliation"]["pending"] == 1


class _RecordingJobStore(InMemoryJobStore):
    def __init__(self, sink: dict):
        super().__init__()
        self._sink = sink

    def create(self, job: JobRecord) -> None:
        super().create(job)
        self._sink[job.job_id] = job


def _fake_blob_service_client_factory():
    class _FakeBlobServiceClient:
        account_name = "fakeaccount"

        @classmethod
        def from_connection_string(cls, conn_str):
            return cls()

    return _FakeBlobServiceClient


# ---------------------------------------------------------------------------------------
# Reconciliation: the human quality-check pass -- src/reconciliation.py, over HTTP
# ---------------------------------------------------------------------------------------


def _seed_extracted_job(client, monkeypatch, submitted_by: str | None = None) -> str:
    """Runs a real extract call (fake pipeline) so a job + its reconciliation record both exist
    (against a shared in-memory reconciliation store the test can keep driving), and returns the
    new job's id."""
    fake_job_store = InMemoryJobStore()
    monkeypatch.setattr(vercel_app, "build_pipeline", lambda settings: (_FakePipeline(), fake_job_store, object()))
    monkeypatch.setattr(vercel_app, "get_container", lambda blob_service, name: _FakeContainer())
    _patch_reconciliation(monkeypatch)

    data = {"file": (io.BytesIO(b"%PDF-1.4 fake"), "dwg.pdf", "application/pdf")}
    if submitted_by:
        data["submittedBy"] = submitted_by
    resp = client.post(
        "/api/drawings/extract", data=data, headers={"x-api-key": "test-shared-secret"}, content_type="multipart/form-data"
    )
    assert resp.status_code == 200
    return resp.get_json()["job_id"]


def test_review_balloon_confirm_marks_reconciled(client, monkeypatch):
    job_id = _seed_extracted_job(client, monkeypatch)

    resp = client.post(
        f"/api/drawings/{job_id}/balloons/1/1/review",
        json={"reviewerId": "bob", "action": "confirm"},
        headers={"x-api-key": "test-shared-secret"},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "reconciled"
    assert body["reviewer_id"] == "bob"


def test_review_balloon_rejects_invalid_action(client, monkeypatch):
    job_id = _seed_extracted_job(client, monkeypatch)

    resp = client.post(
        f"/api/drawings/{job_id}/balloons/1/1/review",
        json={"reviewerId": "bob", "action": "approve"},
        headers={"x-api-key": "test-shared-secret"},
    )

    assert resp.status_code == 400
    assert resp.json["error"] == "ValidationError"


def test_review_balloon_404s_for_unknown_job(client, monkeypatch):
    _patch_reconciliation(monkeypatch)

    resp = client.post(
        "/api/drawings/does-not-exist/balloons/1/1/review",
        json={"reviewerId": "bob", "action": "confirm"},
        headers={"x-api-key": "test-shared-secret"},
    )

    assert resp.status_code == 404
    assert resp.json["error"] == "NotFound"


def test_review_balloon_403s_when_reviewer_is_the_submitter(client, monkeypatch):
    job_id = _seed_extracted_job(client, monkeypatch, submitted_by="alice")

    resp = client.post(
        f"/api/drawings/{job_id}/balloons/1/1/review",
        json={"reviewerId": "alice", "action": "confirm"},
        headers={"x-api-key": "test-shared-secret"},
    )

    assert resp.status_code == 403
    assert resp.json["error"] == "SegregationOfDutiesViolation"


def test_get_reconciliation_state_returns_full_record(client, monkeypatch):
    job_id = _seed_extracted_job(client, monkeypatch)

    resp = client.get(f"/api/drawings/{job_id}/reconciliation", headers={"x-api-key": "test-shared-secret"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["job_id"] == job_id
    assert len(body["balloons"]) == 1
    assert body["balloons"][0]["status"] == "pending"


def test_signoff_returns_409_with_open_balloons_when_incomplete(client, monkeypatch):
    job_id = _seed_extracted_job(client, monkeypatch)

    resp = client.post(f"/api/drawings/{job_id}/signoff", json={"signerId": "bob"}, headers={"x-api-key": "test-shared-secret"})

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["error"] == "IncompleteReconciliation"
    assert body["openBalloons"] == [{"page": 1, "balloonNumber": 1}]


def test_export_blocked_until_signed_off(client, monkeypatch):
    job_id = _seed_extracted_job(client, monkeypatch)

    resp = client.post(f"/api/drawings/{job_id}/export", headers={"x-api-key": "test-shared-secret"})

    assert resp.status_code == 409
    assert resp.json["error"] == "IncompleteReconciliation"


def test_full_reconciliation_flow_review_signoff_then_export(client, monkeypatch):
    job_id = _seed_extracted_job(client, monkeypatch, submitted_by="alice")

    review_resp = client.post(
        f"/api/drawings/{job_id}/balloons/1/1/review",
        json={"reviewerId": "bob", "action": "confirm"},
        headers={"x-api-key": "test-shared-secret"},
    )
    assert review_resp.status_code == 200

    signoff_resp = client.post(
        f"/api/drawings/{job_id}/signoff", json={"signerId": "bob"}, headers={"x-api-key": "test-shared-secret"}
    )
    assert signoff_resp.status_code == 200
    assert signoff_resp.json["signed_off"] is True

    monkeypatch.setattr(
        vercel_app, "write_export_and_get_sas", lambda blob_service, job_id, excel_bytes, conn_str: "https://example/export.xlsx"
    )
    monkeypatch.setattr(vercel_app, "BlobServiceClient", _fake_blob_service_client_factory())

    export_resp = client.post(f"/api/drawings/{job_id}/export", headers={"x-api-key": "test-shared-secret"})

    assert export_resp.status_code == 200
    assert export_resp.json["exportUrl"] == "https://example/export.xlsx"


def test_signoff_403s_when_signer_is_the_submitter(client, monkeypatch):
    job_id = _seed_extracted_job(client, monkeypatch, submitted_by="alice")
    client.post(
        f"/api/drawings/{job_id}/balloons/1/1/review",
        json={"reviewerId": "bob", "action": "confirm"},
        headers={"x-api-key": "test-shared-secret"},
    )

    resp = client.post(f"/api/drawings/{job_id}/signoff", json={"signerId": "alice"}, headers={"x-api-key": "test-shared-secret"})

    assert resp.status_code == 403
    assert resp.json["error"] == "SegregationOfDutiesViolation"
