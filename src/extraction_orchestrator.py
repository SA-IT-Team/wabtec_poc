"""ExtractionOrchestrator + IExtractionStrategy (Strategy pattern, architecture-poc.md §2.2).

VisionGroundedExtractionStrategy sends the page image plus the Document Intelligence text layer to
Azure OpenAI and validates the response against BalloonExtractionResponse. On a malformed/non-schema
response it does one repair re-prompt; if that also fails it returns an empty list rather than
raising, so the orchestrator can still emit a row for every detected candidate (FR-10: a balloon
Document Intelligence found on the page must never simply vanish from the output).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from src.azure_clients import IChatCompletionClient, decode_image_b64
from src.exceptions import AzureOpenAIError
from src.models import BalloonExtractionResponse, ExtractedBalloon
from src.preprocessor import PageImage

logger = logging.getLogger(__name__)

BALLOON_JSON_SCHEMA = BalloonExtractionResponse.model_json_schema()


class IExtractionStrategy(ABC):
    @abstractmethod
    def extract(self, page: PageImage, layout_text: str) -> list[ExtractedBalloon]:
        """Returns whatever balloons the model was able to extract for this page (may be an empty
        list on unrecoverable failure -- never raises for a *content* problem, only propagates
        AzureOpenAIError for a transport/API failure)."""


class VisionGroundedExtractionStrategy(IExtractionStrategy):
    def __init__(self, chat_client: IChatCompletionClient, max_repair_attempts: int = 1):
        self._chat = chat_client
        self._max_repair_attempts = max_repair_attempts

    def extract(self, page: PageImage, layout_text: str) -> list[ExtractedBalloon]:
        image_b64 = decode_image_b64(page.png_bytes)
        prompt_suffix = ""
        last_error: Exception | None = None

        for attempt in range(self._max_repair_attempts + 1):
            try:
                raw = self._chat.extract_balloons(
                    image_b64=image_b64,
                    layout_text=layout_text + prompt_suffix,
                    page_number=page.page_number,
                    json_schema=BALLOON_JSON_SCHEMA,
                )
                parsed = BalloonExtractionResponse.model_validate(raw)
                return parsed.balloons
            except AzureOpenAIError:
                raise  # transport/API failure -- not something a repair re-prompt can fix
            except (PydanticValidationError, TypeError, KeyError) as exc:
                last_error = exc
                logger.warning(
                    "Extraction response failed schema validation on page %d (attempt %d): %s",
                    page.page_number,
                    attempt,
                    exc,
                )
                prompt_suffix = (
                    f"\n\nYour previous response was invalid: {exc}. "
                    "Return valid JSON matching the schema exactly."
                )

        logger.error(
            "Extraction gave up after %d attempts on page %d: %s",
            self._max_repair_attempts + 1,
            page.page_number,
            last_error,
        )
        return []


class ExtractionOrchestrator:
    def __init__(self, strategy: IExtractionStrategy):
        self._strategy = strategy

    def extract_page(
        self, page: PageImage, layout: dict[str, Any], detected_candidates: list[dict[str, Any]]
    ) -> list[ExtractedBalloon]:
        layout_text = self._flatten_layout_text(layout)
        extracted = self._strategy.extract(page, layout_text)
        return self._reconcile_with_candidates(extracted, detected_candidates, page.page_number)

    @staticmethod
    def _flatten_layout_text(layout: dict[str, Any]) -> str:
        return "\n".join(line.get("content", "") for line in layout.get("lines", []))

    @staticmethod
    def _reconcile_with_candidates(
        extracted: list[ExtractedBalloon], candidates: list[dict[str, Any]], page_number: int
    ) -> list[ExtractedBalloon]:
        """FR-10: every balloon Document Intelligence detected must appear in the output, even if
        the extraction model missed it -- as a confidence=0 placeholder, never silently dropped."""
        extracted_numbers = {b.balloon_number for b in extracted}
        result = list(extracted)
        for candidate in candidates:
            if candidate["balloon_number"] not in extracted_numbers:
                result.append(
                    ExtractedBalloon(
                        balloon_number=candidate["balloon_number"],
                        page=page_number,
                        confidence=0.0,
                        extraction_error="Detected by layout analysis but not returned by the extraction model.",
                    )
                )
        return sorted(result, key=lambda b: b.balloon_number)
