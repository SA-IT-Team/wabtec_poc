"""Wires the extraction pipeline (and the reconciliation service) together from Settings. Called
by app.py's routes so every route builds identically-configured collaborators.
"""
from __future__ import annotations

from azure.storage.blob import BlobServiceClient

from src.ai_clients import AzureDocumentIntelligenceClient, ClaudeChatClient
from src.balloon_detector import BalloonDetector
from src.config import Settings
from src.excel_writer import ExcelWriter
from src.extraction_orchestrator import ExtractionOrchestrator, VisionGroundedExtractionStrategy
from src.job_store import TableStorageJobStore
from src.pipeline import ExtractionPipeline
from src.preprocessor import DrawingPreprocessor
from src.reconciliation import ReconciliationService
from src.reconciliation_store import BlobReconciliationStore
from src.tolerance_normalizer import ToleranceNormalizer


def build_pipeline(settings: Settings) -> tuple[ExtractionPipeline, TableStorageJobStore, BlobServiceClient]:
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


def build_reconciliation_service(settings: Settings) -> ReconciliationService:
    return ReconciliationService(BlobReconciliationStore(settings.storage_connection_string))
