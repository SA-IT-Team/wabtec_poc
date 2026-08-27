"""Wires the extraction pipeline together from Settings. Shared by every HTTP entry point (Azure
Functions' function_app.py and Vercel's app.py) so the two hosts can never wire up the
pipeline differently by accident.
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
