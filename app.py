"""The whole backend: a Flask WSGI app, deployed to Vercel, wrapping the pipeline in src/.

Vercel detects Flask via `flask` in requirements.txt and routes every request straight to the
`app` object defined here (framework-preset mode) -- no vercel.json rewrites needed, the
@app.route decorators below ARE the routing. This file must live at the project root (or src/ or
app/) for Vercel to find it; it does NOT live under /api/, which is a different (file-based,
non-framework) Vercel convention this app doesn't use. See deployment-vercel.md.

    POST /api/drawings/extract          -- upload + synchronously process one SMALL drawing
                                            (multipart, well under Vercel's 4.5MB body cap)
    POST /api/drawings/upload-url       -- get a direct-to-blob SAS URL for a LARGE drawing
    POST /api/drawings/<jobId>/process  -- process a drawing already uploaded via the SAS above
    GET  /api/drawings/<jobId>          -- re-fetch a previously computed result
    GET  /api/health                   -- unauthenticated liveness check

Why two upload paths: Vercel Functions cap request/response bodies at 4.5MB (platform limit, not
configurable) -- see https://vercel.com/docs/functions/limitations#request-body-size. Real
multi-page ballooned drawings routinely exceed that. The SAS-based path works around the cap by
never routing the file's bytes through this function at all -- the browser uploads directly to
Blob Storage, and this app only ever sees a blob path reference.

Azure is still used for two *services* -- Document Intelligence (layout/OCR) and Storage (Blob for
files, Table for job records) -- but nothing here runs on Azure compute. Claude (Anthropic Messages
API) does the structured extraction.

Auth: there is no platform-level gate in front of a Vercel Function, so this app enforces its own
shared-secret header (API_ACCESS_KEY / x-api-key) on every route except /api/health. That is
POC-grade only -- see deployment-vercel.md §3 for what it explicitly is not.

Run locally with `python app.py` (or `flask --app app run --port 8000`); .env is loaded below.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from functools import wraps

from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request

# Local dev convenience: reads .env from the project root if present. On Vercel there is no .env
# file (env vars come from the project settings), so this is a no-op there.
load_dotenv()

from src.config import Settings
from src.exceptions import (
    ExtractionServiceError,
    JobNotFoundError,
    PageLimitExceededError,
    QualityThresholdError,
    UnsupportedFileTypeError,
    ValidationError,
)
from src.job_store import TableStorageJobStore
from src.models import JobRecord, JobStatus
from src.pipeline import ExtractionPipeline, PipelineContext
from src.pipeline_factory import build_pipeline
from src.storage_helpers import generate_upload_sas, get_container, read_blob_bytes, write_export_and_get_sas
from src.upload_handler import SUPPORTED_CONTENT_TYPES, UploadHandler, validate_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# CORS: Vercel has no built-in per-function CORS gate -- handled here instead. Set
# CORS_ALLOWED_ORIGIN to your actual frontend origin before sharing a live URL; "*" is a POC-only
# default, same spirit as the missing user auth (see module docstring). The "*" default is also
# what makes `npm run dev` on localhost:5173 able to call a locally-running backend.
ALLOWED_ORIGIN = os.environ.get("CORS_ALLOWED_ORIGIN", "*")


@app.after_request
def _add_cors_headers(response: Response) -> Response:
    response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, x-api-key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def _require_api_key(fn):
    """Nothing gates a Vercel Function at the platform level, so this app enforces its own
    shared-secret header. API_ACCESS_KEY is deliberately NOT optional here (unlike most Settings
    fields, it's checked eagerly per-request, not just at cold start) -- an unset key means every
    request 500s with a clear reason rather than the endpoint silently running wide open."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":  # CORS preflight -- never gated
            return "", 204
        configured_key = os.environ.get("API_ACCESS_KEY")
        if not configured_key:
            logger.error("API_ACCESS_KEY is not configured")
            return _json_error("ConfigurationError", "Missing required environment variable: API_ACCESS_KEY", 500)
        if request.headers.get("x-api-key") != configured_key:
            return _json_error("Unauthorized", "Missing or invalid x-api-key header.", 401)
        return fn(*args, **kwargs)

    return wrapper


def _json_error(error: str, message: str, status: int):
    return jsonify({"error": error, "message": message}), status


def _map_pipeline_error(exc: Exception):
    if isinstance(exc, UnsupportedFileTypeError):
        return _json_error("UnsupportedFileType", str(exc), 400)
    if isinstance(exc, PageLimitExceededError):
        return _json_error("PageLimitExceeded", str(exc), 413)
    if isinstance(exc, QualityThresholdError):
        return _json_error("QualityThresholdNotMet", str(exc), 422)
    if isinstance(exc, ValidationError):
        return _json_error("ValidationError", str(exc), 400)
    if isinstance(exc, ExtractionServiceError):
        logger.exception("Upstream extraction service failure")
        return _json_error("ExtractionServiceError", str(exc), 502)
    logger.exception("Unhandled error processing drawing")
    return _json_error("InternalError", "An unexpected error occurred.", 500)


def _finish_job(job_store, blob_service, job: JobRecord, ctx: PipelineContext, settings: Settings):
    """Shared tail end of both extraction paths: export, job bookkeeping, response shaping."""
    export_url = write_export_and_get_sas(blob_service, job.job_id, ctx.excel_bytes, settings.storage_connection_string)

    job.status = JobStatus.COMPLETE
    job.balloon_count_detected = ctx.balloon_count_detected
    job.balloon_count_extracted = len([b for b in ctx.balloons if b.extraction_error is None])
    job.avg_confidence = sum(b.confidence for b in ctx.balloons) / len(ctx.balloons) if ctx.balloons else None
    job.completed_at = datetime.now(timezone.utc)
    job_store.update(job)

    result = ExtractionPipeline.to_result(ctx, export_url)
    return Response(result.model_dump_json(), status=200, mimetype="application/json")


@app.route("/api/drawings/extract", methods=["POST", "OPTIONS"])
@_require_api_key
def extract_drawing():
    """Small-file convenience path: one multipart POST, synchronous result. Only safe for files
    comfortably under Vercel's 4.5MB request body cap -- for anything larger, use
    POST /api/drawings/upload-url followed by POST /api/drawings/<jobId>/process instead."""
    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        logger.exception("Configuration error")
        return _json_error("ConfigurationError", str(exc), 500)

    uploaded = request.files.get("file")
    if uploaded is None:
        return _json_error("ValidationError", "Missing 'file' in multipart form data.", 400)

    file_bytes = uploaded.read()
    content_type = uploaded.mimetype or "application/octet-stream"

    try:
        pipeline, job_store, blob_service = build_pipeline(settings)
        upload_handler = UploadHandler(lambda name: get_container(blob_service, name), job_store)
        job = upload_handler.handle_upload(file_bytes=file_bytes, file_name=uploaded.filename, content_type=content_type)

        ctx = PipelineContext(job=job, file_bytes=file_bytes, content_type=content_type)
        ctx = pipeline.run(ctx)

        return _finish_job(job_store, blob_service, job, ctx, settings)
    except Exception as exc:  # noqa: BLE001 - mapped by type inside _map_pipeline_error
        return _map_pipeline_error(exc)


@app.route("/api/drawings/upload-url", methods=["POST", "OPTIONS"])
@_require_api_key
def create_upload_url():
    """Step 1 of the large-file path: returns a write-only SAS URL the client PUTs the file to
    directly. This function never sees the file's bytes, which is exactly the point -- see the
    module docstring on why that matters for Vercel specifically."""
    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        logger.exception("Configuration error")
        return _json_error("ConfigurationError", str(exc), 500)

    payload = request.get_json(silent=True) or {}
    file_name = payload.get("fileName")
    content_type = payload.get("contentType")
    if not file_name or not content_type:
        return _json_error("ValidationError", "Request body must include 'fileName' and 'contentType'.", 400)
    if content_type not in SUPPORTED_CONTENT_TYPES:
        return _json_error(
            "UnsupportedFileType",
            f"Unsupported content type '{content_type}'. Supported: {', '.join(sorted(SUPPORTED_CONTENT_TYPES))}.",
            400,
        )

    job_id = str(uuid.uuid4())
    blob_name = f"{job_id}/source_{file_name}"

    blob_service = BlobServiceClient.from_connection_string(settings.storage_connection_string)
    upload_url = generate_upload_sas(blob_service, settings.storage_connection_string, "raw-drawings", blob_name)

    job = JobRecord(job_id=job_id, file_name=file_name, status=JobStatus.PROCESSING, created_at=datetime.now(timezone.utc))
    TableStorageJobStore(settings.storage_connection_string).create(job)

    return jsonify(
        {
            "jobId": job_id,
            "uploadUrl": upload_url,
            "blobPath": blob_name,
            # PUT the raw file bytes here with header 'x-ms-blob-type: BlockBlob', then call
            # POST /api/drawings/<jobId>/process with {"blobPath": ..., "contentType": ...}.
        }
    ), 201


@app.route("/api/drawings/<job_id>/process", methods=["POST", "OPTIONS"])
@_require_api_key
def process_drawing(job_id: str):
    """Step 2 of the large-file path: reads the already-uploaded blob server-side (no request-body
    size constraint applies to that read -- it's this function talking to Blob Storage, not the
    client talking to this function) and runs the same pipeline as extract_drawing."""
    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        logger.exception("Configuration error")
        return _json_error("ConfigurationError", str(exc), 500)

    payload = request.get_json(silent=True) or {}
    blob_path = payload.get("blobPath")
    content_type = payload.get("contentType")
    if not blob_path or not content_type:
        return _json_error("ValidationError", "Request body must include 'blobPath' and 'contentType'.", 400)

    try:
        pipeline, job_store, blob_service = build_pipeline(settings)
        job = job_store.get(job_id)

        file_bytes = read_blob_bytes(blob_service, "raw-drawings", blob_path)
        validate_file(file_bytes, content_type)

        ctx = PipelineContext(job=job, file_bytes=file_bytes, content_type=content_type)
        ctx = pipeline.run(ctx)

        return _finish_job(job_store, blob_service, job, ctx, settings)
    except JobNotFoundError:
        return _json_error("NotFound", f"No job found for id '{job_id}'.", 404)
    except Exception as exc:  # noqa: BLE001 - mapped by type inside _map_pipeline_error
        return _map_pipeline_error(exc)


@app.route("/api/drawings/<job_id>", methods=["GET", "OPTIONS"])
@_require_api_key
def get_drawing_result(job_id: str):
    try:
        settings = Settings.from_env()
        job_store = TableStorageJobStore(settings.storage_connection_string)
        job = job_store.get(job_id)
        return Response(job.model_dump_json(), status=200, mimetype="application/json")
    except JobNotFoundError:
        return _json_error("NotFound", f"No job found for id '{job_id}'.", 404)
    except RuntimeError as exc:
        logger.exception("Configuration error")
        return _json_error("ConfigurationError", str(exc), 500)
    except Exception:  # noqa: BLE001
        logger.exception("Unhandled error fetching job")
        return _json_error("InternalError", "An unexpected error occurred.", 500)


@app.route("/api/health", methods=["GET"])
def health():
    """Unauthenticated liveness check -- deliberately outside _require_api_key so uptime
    monitoring doesn't need the shared secret. Reveals nothing but "the process is up"."""
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    # Local development only. Vercel imports the `app` object above and serves it itself, so this
    # block never runs in a deployment. Debug is off deliberately: the reloader double-imports
    # this module, which doubles cold-start cost for no benefit on a request-per-minute POC.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
