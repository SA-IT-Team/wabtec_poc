"""Integration tests: wire DrawingPreprocessor -> BalloonDetector -> ExtractionOrchestrator ->
ToleranceNormalizer -> ExcelWriter together exactly as function_app.py does, using Fake* Azure
clients (no network calls) so the whole pipeline is exercised end-to-end in-process.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from openpyxl import load_workbook

from src.ai_clients import FakeChatCompletionClient, FakeDocumentAnalysisClient
from src.balloon_detector import BalloonDetector
from src.excel_writer import ExcelWriter
from src.exceptions import DocumentIntelligenceError
from src.extraction_orchestrator import ExtractionOrchestrator, VisionGroundedExtractionStrategy
from src.models import JobRecord, JobStatus
from src.pipeline import ExtractionPipeline, PipelineContext
from src.preprocessor import DrawingPreprocessor
from src.tolerance_normalizer import ToleranceNormalizer

import pytest


def _build_pipeline(di_client, chat_client) -> ExtractionPipeline:
    return ExtractionPipeline(
        preprocessor=DrawingPreprocessor(target_dpi=150, min_dpi=72, max_pages=10),
        balloon_detector=BalloonDetector(di_client),
        orchestrator=ExtractionOrchestrator(VisionGroundedExtractionStrategy(chat_client)),
        normalizer=ToleranceNormalizer(),
        excel_writer=ExcelWriter(),
    )


def _job() -> JobRecord:
    return JobRecord(job_id="job-int-1", file_name="dwg.pdf", status=JobStatus.PROCESSING, created_at=datetime.now(timezone.utc))


def test_end_to_end_happy_path_produces_matching_counts_and_valid_excel(
    make_pdf_bytes, sample_layout, sample_extraction_response
):
    di_client = FakeDocumentAnalysisClient(sample_layout)
    chat_client = FakeChatCompletionClient([sample_extraction_response])
    pipeline = _build_pipeline(di_client, chat_client)

    ctx = PipelineContext(
        job=_job(), file_bytes=make_pdf_bytes(page_count=1), content_type="application/pdf",
        drawing_number="DWG-10245", revision="C",
    )
    ctx = pipeline.run(ctx)
    result = ExtractionPipeline.to_result(ctx, export_url="https://example.blob/exports/job-int-1.xlsx")

    assert result.balloon_count_detected == 2  # balloons 12 and 13 from the fixture layout
    assert result.balloon_count_extracted == 2
    assert result.balloon_count_mismatch is False
    assert {b.balloon_number for b in result.balloons} == {12, 13}

    wb = load_workbook(io.BytesIO(ctx.excel_bytes))
    ws = wb.active
    assert ws.max_row == 3  # header + 2 balloon rows


def test_model_missing_a_detected_balloon_is_surfaced_not_dropped(
    make_pdf_bytes, sample_layout
):
    """FR-10 end-to-end: Document Intelligence detects 2 candidates on the page, but the AI model
    only returns 1 of them. The missing balloon must still appear in the final result (as a
    confidence=0 placeholder) and the mismatch flag must be set -- this is the exact signal the
    POC exists to measure."""
    di_client = FakeDocumentAnalysisClient(sample_layout)  # detects balloons 12 and 13
    partial_response = {
        "balloons": [
            {"balloon_number": 12, "page": 1, "nominal_value": 25.4, "unit": "mm", "confidence": 0.9}
        ]
    }
    chat_client = FakeChatCompletionClient([partial_response])
    pipeline = _build_pipeline(di_client, chat_client)

    ctx = PipelineContext(job=_job(), file_bytes=make_pdf_bytes(page_count=1), content_type="application/pdf")
    ctx = pipeline.run(ctx)
    result = ExtractionPipeline.to_result(ctx, export_url=None)

    assert result.balloon_count_detected == 2
    assert result.balloon_count_extracted == 1
    assert result.balloon_count_mismatch is True

    missing = next(b for b in result.balloons if b.balloon_number == 13)
    assert missing.confidence == 0.0
    assert missing.extraction_error is not None


def test_document_intelligence_outage_propagates_as_extraction_service_error(make_pdf_bytes):
    di_client = FakeDocumentAnalysisClient(RuntimeError("503 from upstream"))
    chat_client = FakeChatCompletionClient([])  # never reached
    pipeline = _build_pipeline(di_client, chat_client)

    ctx = PipelineContext(job=_job(), file_bytes=make_pdf_bytes(page_count=1), content_type="application/pdf")

    with pytest.raises(DocumentIntelligenceError):
        pipeline.run(ctx)


def test_multi_page_drawing_aggregates_balloons_across_pages(make_pdf_bytes):
    layout_page = {"lines": [], "words": [{"content": "1", "confidence": 0.9}]}
    di_client = FakeDocumentAnalysisClient(layout_page)
    responses = [
        {"balloons": [{"balloon_number": 1, "page": 1, "nominal_value": 5.0, "confidence": 0.9}]},
        {"balloons": [{"balloon_number": 1, "page": 2, "nominal_value": 7.0, "confidence": 0.9}]},
    ]
    chat_client = FakeChatCompletionClient(responses)
    pipeline = _build_pipeline(di_client, chat_client)

    ctx = PipelineContext(job=_job(), file_bytes=make_pdf_bytes(page_count=2), content_type="application/pdf")
    ctx = pipeline.run(ctx)

    assert len(ctx.balloons) == 2
    assert {b.page for b in ctx.balloons} == {1, 2}
