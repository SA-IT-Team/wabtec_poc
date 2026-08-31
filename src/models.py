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


# --------------------------------------------------------------------------------------
# Reconciliation: the human quality-check pass every balloon must go through before its
# drawing can be exported. See src/reconciliation.py for the state machine and business rules.
# Defined ahead of ExtractionResult, which embeds ReconciliationStatus.
# --------------------------------------------------------------------------------------


class ReviewAction(str, Enum):
    CONFIRM = "confirm"  # reviewer agrees with the extracted value as-is
    CORRECT = "correct"  # reviewer supplies a different value
    CANNOT_DETERMINE = "cannot_determine"  # reviewer can't tell from the source -- not a guess


class BalloonReviewStatus(str, Enum):
    PENDING = "pending"  # not yet reviewed by anyone
    RECONCILED = "reconciled"  # reviewed; confirmed or corrected -- counts toward 100%
    CANNOT_DETERMINE = "cannot_determine"  # reviewed but flagged undeterminable -- still blocks sign-off


class BalloonReviewRecord(BaseModel):
    """One balloon's reconciliation state. `extracted` is the immutable AI-produced snapshot;
    `reviewed` is what a human confirmed or corrected it to -- the export (once signed off) is
    built from `reviewed` where present, `extracted` otherwise (see ReconciliationService)."""

    page: int
    balloon_number: int
    extracted: ExtractedBalloon
    reviewed: Optional[ExtractedBalloon] = None
    status: BalloonReviewStatus = BalloonReviewStatus.PENDING
    discrepancy: bool = False  # True iff a reviewer's value differs from the extracted one (FR-23)
    reviewer_id: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    notes: Optional[str] = None


class ReconciliationRecord(BaseModel):
    """The full reconciliation state for one drawing revision -- persisted as a single JSON blob
    (see src/reconciliation_store.py); this IS the source of truth for what gets exported."""

    job_id: str
    drawing_number: Optional[str] = None
    revision: Optional[str] = None
    submitted_by: Optional[str] = None  # self-declared; see reconciliation.py's module docstring
    # Export template chosen at upload time (src/excel_templates.py); None means "use the
    # registry's default". The export endpoint may still override it per call -- see app.py.
    template_id: Optional[str] = None
    balloons: list[BalloonReviewRecord]
    signed_off: bool = False
    signed_off_by: Optional[str] = None
    signed_off_at: Optional[datetime] = None
    created_at: datetime


class ReconciliationStatus(BaseModel):
    """Summary shape for GET /api/drawings/{jobId}/reconciliation and embedded in ExtractionResult."""

    job_id: str
    total_balloons: int
    pending: int
    reconciled: int
    cannot_determine: int
    percent_complete: float  # (reconciled + cannot_determine) / total, in [0, 100]
    ready_for_signoff: bool  # every balloon is `reconciled` (cannot_determine still blocks, see above)
    signed_off: bool
    signed_off_by: Optional[str] = None
    signed_off_at: Optional[datetime] = None


class ExtractionResult(BaseModel):
    """Response body for POST /api/drawings/extract (architecture-poc.md §3.2).

    `export_url` is always null on this response now -- extraction produces a *draft* only.
    Nothing is exportable until every balloon has been reviewed and the drawing signed off
    (requirements.md's "quality check/reconciliation to ensure 100% data accuracy" item,
    see src/reconciliation.py and `reconciliation` below).
    """

    job_id: str
    drawing_number: Optional[str] = None
    revision: Optional[str] = None
    balloon_count_detected: int
    balloon_count_extracted: int
    balloon_count_mismatch: bool
    balloons: list[ExtractedBalloon]
    export_url: Optional[str] = None
    reconciliation: Optional[ReconciliationStatus] = None


# --------------------------------------------------------------------------------------
# Chatbot analysis: the "general analysis" / feedback assistant requirements.md asks for --
# missing information, incomplete data, inconsistencies between sheets, common mistakes. See
# src/analysis_rules.py (deterministic checks) and src/chat_assistant.py (AI review + free chat).
# --------------------------------------------------------------------------------------


class AnalysisFindingCategory(str, Enum):
    MISSING_INFO = "missing_info"  # a field that should have a value doesn't
    INCOMPLETE_DATA = "incomplete_data"  # extraction failed, was blocked, or is low-confidence
    INCONSISTENCY = "inconsistency"  # conflicting data within the drawing (e.g. across sheets)
    COMMON_MISTAKE = "common_mistake"  # a recognizable authoring/extraction footgun, not a hard error


class AnalysisFindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AnalysisFindingSource(str, Enum):
    RULE = "rule"  # produced by src/analysis_rules.py -- deterministic, no API call, always reproducible
    AI = "ai"  # produced by the Claude review pass -- see chat_assistant.py


class BalloonRef(BaseModel):
    page: int
    balloon_number: int


class AnalysisFinding(BaseModel):
    category: AnalysisFindingCategory
    severity: AnalysisFindingSeverity = AnalysisFindingSeverity.WARNING
    summary: str
    detail: str
    balloon_refs: list[BalloonRef] = Field(default_factory=list)
    source: AnalysisFindingSource = AnalysisFindingSource.RULE


class AnalysisReport(BaseModel):
    """Response body for POST /api/drawings/{jobId}/analyze -- the chatbot's structured feedback
    pass over the current reconciliation state. Re-runnable at any point in review; always
    reflects current state (including reviewer corrections), nothing is cached."""

    job_id: str
    generated_at: datetime
    summary: str
    findings: list[AnalysisFinding] = Field(default_factory=list)
