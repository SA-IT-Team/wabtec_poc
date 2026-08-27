"""Azure Functions v2 (Python) app: the two HTTP endpoints from architecture-poc.md §3.2.

    POST /api/drawings/extract   -- upload + synchronously process one drawing
    GET  /api/drawings/{jobId}   -- re-fetch a previously computed result

Auth: Function-level key (`x-functions-key` / `?code=`), configured via AuthLevel.FUNCTION below --
explicitly NOT production auth. See architecture-poc.md §3.3 and architecture-full.md §3.3 for the
Entra ID model this is deliberately deferring.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import azure.functions as func
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas

from src.ai_clients import AzureDocumentIntelligenceClient, ClaudeChatClient
from src.balloon_detector import BalloonDetector
from src.config import Settings
from src.excel_writer import ExcelWriter
from src.exceptions import (
    ExtractionServiceError,
    JobNotFoundError,
    PageLimitExceededError,
    QualityThresholdError,
    UnsupportedFileTypeError,
    ValidationError,
)
from src.extraction_orchestrator import ExtractionOrchestrator, VisionGroundedExtractionStrategy
from src.job_store import TableStorageJobStore
from src.models import JobStatus
from src.pipeline import ExtractionPipeline, PipelineContext
from src.preprocessor import DrawingPreprocessor
from src.tolerance_normalizer import ToleranceNormalizer
from src.upload_handler import UploadHandler

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
logger = logging.getLogger(__name__)


def _json_response(payload: dict, status: int) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload, default=str), status_code=status, mimetype="application/json")


def _build_pipeline(settings: Settings) -> tuple[ExtractionPipeline, TableStorageJobStore, BlobServiceClient]:
    di_client = AzureDocumentIntelligenceClient(settings.doc_intelligence_endpoint, settings.doc_intelligence_key)
    chat_client = ClaudeChatClient(
        settings.claude_api_key,
        settings.claude_model,
        settings.claude_max_tokens,
        settings.claude_api_base_url,
    )
    pipeline = ExtractionPipeline(
        preprocessor=DrawingPreprocessor(
            target_dpi=settings.target_dpi, min_dpi=settings.min_dpi, max_pages=settings.max_pages
        ),
        balloon_detector=BalloonDetector(di_client),
        orchestrator=ExtractionOrchestrator(VisionGroundedExtractionStrategy(chat_client)),
        normalizer=ToleranceNormalizer(),
        excel_writer=ExcelWriter(),
    )
    job_store = TableStorageJobStore(settings.storage_connection_string)
    blob_service = BlobServiceClient.from_connection_string(settings.storage_connection_string)
    return pipeline, job_store, blob_service


class _ContainerAdapter:
    """Adapts a BlobContainerClient to the small `upload_blob(name, data, overwrite)` shape
    UploadHandler depends on (dependency inversion, keeps UploadHandler SDK-agnostic for tests)."""

    def __init__(self, client):
        self._client = client

    def upload_blob(self, blob_name: str, data: bytes, overwrite: bool = True) -> None:
        self._client.upload_blob(name=blob_name, data=data, overwrite=overwrite)


def _container(blob_service: BlobServiceClient, name: str) -> _ContainerAdapter:
    client = blob_service.get_container_client(name)
    if not client.exists():
        client.create_container()
    return _ContainerAdapter(client)


def _parse_account_key(connection_string: str) -> str:
    parts = dict(p.split("=", 1) for p in connection_string.split(";") if "=" in p)
    return parts["AccountKey"]


def _write_export_and_get_sas(blob_service: BlobServiceClient, job_id: str, excel_bytes: bytes, settings: Settings) -> str:
    blob_name = f"{job_id}/characteristics.xlsx"
    _container(blob_service, "exports").upload_blob(blob_name, excel_bytes, overwrite=True)

    blob_client = blob_service.get_blob_client(container="exports", blob=blob_name)
    sas_token = generate_blob_sas(
        account_name=blob_service.account_name,
        container_name="exports",
        blob_name=blob_name,
        account_key=_parse_account_key(settings.storage_connection_string),
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    return f"{blob_client.url}?{sas_token}"


@app.route(route="drawings/extract", methods=["POST"])
def extract_drawing(req: func.HttpRequest) -> func.HttpResponse:
    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        logger.exception("Configuration error")
        return _json_response({"error": "ConfigurationError", "message": str(exc)}, 500)

    uploaded = req.files.get("file") if req.files else None
    if uploaded is None:
        return _json_response({"error": "ValidationError", "message": "Missing 'file' in multipart form data."}, 400)

    file_bytes = uploaded.read()
    content_type = uploaded.content_type or "application/octet-stream"

    try:
        pipeline, job_store, blob_service = _build_pipeline(settings)
        upload_handler = UploadHandler(lambda name: _container(blob_service, name), job_store)
        job = upload_handler.handle_upload(file_bytes=file_bytes, file_name=uploaded.filename, content_type=content_type)

        ctx = PipelineContext(job=job, file_bytes=file_bytes, content_type=content_type)
        ctx = pipeline.run(ctx)

        export_url = _write_export_and_get_sas(blob_service, job.job_id, ctx.excel_bytes, settings)

        job.status = JobStatus.COMPLETE
        job.balloon_count_detected = ctx.balloon_count_detected
        job.balloon_count_extracted = len([b for b in ctx.balloons if b.extraction_error is None])
        job.avg_confidence = (
            sum(b.confidence for b in ctx.balloons) / len(ctx.balloons) if ctx.balloons else None
        )
        job.completed_at = datetime.now(timezone.utc)
        job_store.update(job)

        result = ExtractionPipeline.to_result(ctx, export_url)
        return _json_response(json.loads(result.model_dump_json()), 200)

    except UnsupportedFileTypeError as exc:
        return _json_response({"error": "UnsupportedFileType", "message": str(exc)}, 400)
    except PageLimitExceededError as exc:
        return _json_response({"error": "PageLimitExceeded", "message": str(exc)}, 413)
    except QualityThresholdError as exc:
        return _json_response({"error": "QualityThresholdNotMet", "message": str(exc)}, 422)
    except ValidationError as exc:
        return _json_response({"error": "ValidationError", "message": str(exc)}, 400)
    except ExtractionServiceError as exc:
        logger.exception("Upstream extraction service failure")
        return _json_response({"error": "ExtractionServiceError", "message": str(exc)}, 502)
    except Exception:  # noqa: BLE001 - last-resort guard so a bug never leaks a raw 500 stack trace
        logger.exception("Unhandled error processing drawing")
        return _json_response({"error": "InternalError", "message": "An unexpected error occurred."}, 500)


@app.route(route="drawings/{jobId}", methods=["GET"])
def get_drawing_result(req: func.HttpRequest) -> func.HttpResponse:
    job_id = req.route_params.get("jobId", "")
    try:
        settings = Settings.from_env()
        job_store = TableStorageJobStore(settings.storage_connection_string)
        job = job_store.get(job_id)
        return _json_response(json.loads(job.model_dump_json()), 200)
    except JobNotFoundError:
        return _json_response({"error": "NotFound", "message": f"No job found for id '{job_id}'."}, 404)
    except RuntimeError as exc:
        logger.exception("Configuration error")
        return _json_response({"error": "ConfigurationError", "message": str(exc)}, 500)
    except Exception:  # noqa: BLE001
        logger.exception("Unhandled error fetching job")
        return _json_response({"error": "InternalError", "message": "An unexpected error occurred."}, 500)
