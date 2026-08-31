"""Domain exceptions for the ballooned-drawing extraction POC.

Mapped to HTTP status codes at the HTTP boundary (see app.py's _map_pipeline_error) and to the
error-handling table in architecture-poc.md §2.3.
"""


class BdxError(Exception):
    """Base class for all domain errors raised by this service."""


class ValidationError(BdxError):
    """Raised for malformed or unacceptable input. Maps to HTTP 400."""


class UnsupportedFileTypeError(ValidationError):
    """Raised when the uploaded file's content type isn't one we process. Maps to HTTP 400."""


class QualityThresholdError(ValidationError):
    """Raised when a page/image falls below the configured DPI/quality floor (FR-03). Maps to HTTP 422."""


class UnknownTemplateError(ValidationError):
    """Raised when a caller specifies an export `templateId` that isn't registered in
    src/excel_templates.py. Maps to HTTP 400 -- see GET /api/templates for the valid set."""


class PageLimitExceededError(ValidationError):
    """Raised when a document has more pages than the configured per-request cap. Maps to HTTP 413."""


class ExtractionServiceError(BdxError):
    """Base class for failures in an upstream AI service (Azure Document Intelligence or Claude).
    Maps to HTTP 502."""


class DocumentIntelligenceError(ExtractionServiceError):
    """Raised when Azure AI Document Intelligence fails after retries."""


class ClaudeApiError(ExtractionServiceError):
    """Raised when the Claude (Anthropic) API fails after retries."""


class SchemaValidationError(ExtractionServiceError):
    """Raised when a model response can't be coerced into the expected structured-output schema."""


class JobNotFoundError(BdxError):
    """Raised when a job id has no corresponding record. Maps to HTTP 404."""


class BalloonNotFoundError(BdxError):
    """Raised when a (page, balloon_number) pair has no reconciliation record for the given job.
    Maps to HTTP 404."""


class SegregationOfDutiesError(BdxError):
    """Raised when a reviewer/signer id matches the job's self-declared submitter id (FR-22:
    the reviewer must not be the same person who ran the extraction). Maps to HTTP 403.

    "Same person" is self-declared, not authenticated -- see reconciliation.py's module docstring
    for why that's still worth enforcing in a POC with no real identity system."""


class IncompleteReconciliationError(BdxError):
    """Raised on sign-off or export when one or more balloons are not yet `reconciled` (FR-18,
    FR-24). Maps to HTTP 409. Carries the list of still-open (page, balloon_number) pairs so the
    caller can show exactly what's blocking it, not just that something is."""

    def __init__(self, message: str, open_balloons: list[tuple[int, int]]):
        super().__init__(message)
        self.open_balloons = open_balloons
