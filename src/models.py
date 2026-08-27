"""Pydantic data models shared across the pipeline.

`BalloonExtractionResponse` doubles as the JSON Schema we hand to Azure OpenAI's structured-outputs
API (`.model_json_schema()`), so the shape of these models IS the extraction contract -- see
extraction_orchestrator.py.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ToleranceType(str, Enum):
    BILATERAL = "bilateral"
    UNILATERAL = "unilateral"
    LIMIT = "limit"
    GENERAL = "general"
    NONE = "none"


class GdtInfo(BaseModel):
    symbol: str
    value: float
    modifiers: list[str] = Field(default_factory=list)
    datums: list[str] = Field(default_factory=list)


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class ExtractedBalloon(BaseModel):
    balloon_number: int
    page: int = 1
    bounding_box: Optional[BoundingBox] = None
    nominal_value: Optional[float] = None
    unit: Optional[str] = None
    tolerance_type: Optional[ToleranceType] = None
    upper_tol: Optional[float] = None
    lower_tol: Optional[float] = None
    gdt: Optional[GdtInfo] = None
    surface_finish: Optional[str] = None
    notes: Optional[str] = None
    confidence: float = 0.0
    # POC-specific bookkeeping fields -- not part of the AOAI response schema, set locally.
    extraction_error: Optional[str] = None
    blocked: bool = False


class BalloonExtractionResponse(BaseModel):
    """The exact schema requested from Azure OpenAI for one page (architecture-poc.md §3.2)."""

    balloons: list[ExtractedBalloon]


class JobStatus(str, Enum):
    PROCESSING = "Processing"
    COMPLETE = "Complete"
    FAILED = "Failed"


class JobRecord(BaseModel):
    job_id: str
    file_name: str
    status: JobStatus = JobStatus.PROCESSING
    drawing_number: Optional[str] = None
    revision: Optional[str] = None
    balloon_count_detected: int = 0
    balloon_count_extracted: int = 0
    avg_confidence: Optional[float] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_reason: Optional[str] = None


class ExtractionResult(BaseModel):
    """Response body for POST /api/drawings/extract (architecture-poc.md §3.2)."""

    job_id: str
    drawing_number: Optional[str] = None
    revision: Optional[str] = None
    balloon_count_detected: int
    balloon_count_extracted: int
    balloon_count_mismatch: bool
    balloons: list[ExtractedBalloon]
    export_url: Optional[str] = None
