"""Domain exceptions for the ballooned-drawing extraction POC.

Mapped to HTTP status codes at the Azure Function boundary (see function_app.py) and to the
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
