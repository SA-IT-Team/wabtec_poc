"""Adapter pattern (architecture-poc.md §2.2): thin wrappers around the underlying AI service SDKs
behind small interfaces, so ExtractionOrchestrator / BalloonDetector never import an SDK directly
and unit tests can substitute Fake* implementations with no network calls.

Two providers, two different clouds: Document Intelligence stays on Azure (balloon-region OCR/
layout); the structured dimension/tolerance/GD&T extraction call goes to Claude via Anthropic's
Messages API (see ClaudeChatClient below) -- this file's name is deliberately provider-neutral
rather than "azure_clients" for that reason.
"""
from __future__ import annotations

import base64
import logging
from abc import ABC, abstractmethod
from typing import Any

from src.exceptions import ClaudeApiError, DocumentIntelligenceError
from src.retry import external_api_retry

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------
# Document Intelligence
# --------------------------------------------------------------------------------------


class IDocumentAnalysisClient(ABC):
    @abstractmethod
    def analyze_layout(self, image_bytes: bytes) -> dict[str, Any]:
        """Runs layout analysis on one page image and returns a plain dict with (at least)
        `lines: [{content}]` and `words: [{content, polygon, confidence}]`."""


class AzureDocumentIntelligenceClient(IDocumentAnalysisClient):
    """Real implementation backed by azure-ai-documentintelligence's `prebuilt-layout` model."""

    def __init__(self, endpoint: str, api_key: str):
        # Imported lazily so unit tests that only use FakeDocumentAnalysisClient don't require
        # network-capable Azure SDK credentials to be importable in CI.
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential

        self._client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(api_key))

    @external_api_retry((Exception,))
    def analyze_layout(self, image_bytes: bytes) -> dict[str, Any]:
        try:
            poller = self._client.begin_analyze_document(
                model_id="prebuilt-layout",
                body=image_bytes,
                content_type="application/octet-stream",
            )
            result = poller.result()
        except Exception as exc:  # noqa: BLE001 - normalize every SDK failure to our own error type
            raise DocumentIntelligenceError(f"Document Intelligence analysis failed: {exc}") from exc
        return self._serialize(result)

    @staticmethod
    def _serialize(result: Any) -> dict[str, Any]:
        """Flattens the SDK's AnalyzeResult into the plain-dict shape BalloonDetector expects.
        NOTE: field names (`polygon`, `confidence`) track the azure-ai-documentintelligence >=1.0.0
        stable API -- re-check against the pinned SDK version in requirements.txt if upgrading."""
        lines: list[dict[str, Any]] = []
        words: list[dict[str, Any]] = []
        for page in getattr(result, "pages", []) or []:
            for line in getattr(page, "lines", []) or []:
                lines.append({"content": line.content})
            for word in getattr(page, "words", []) or []:
                polygon = getattr(word, "polygon", None)
                words.append(
                    {
                        "content": word.content,
                        "polygon": list(polygon) if polygon else None,
                        "confidence": getattr(word, "confidence", None) or 0.5,
                    }
                )
        return {"lines": lines, "words": words}


class FakeDocumentAnalysisClient(IDocumentAnalysisClient):
    """Test double: returns a canned layout dict (or raises a canned exception)."""

    def __init__(self, canned_result: dict[str, Any] | Exception):
        self._canned = canned_result
        self.calls = 0

    def analyze_layout(self, image_bytes: bytes) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self._canned, Exception):
            raise self._canned
        return self._canned


# --------------------------------------------------------------------------------------
# Claude (Anthropic Messages API)
# --------------------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a mechanical engineering drawing analyst extracting dimension and tolerance data "
    "from a ballooned engineering drawing page. For every balloon visible on the page, return its "
    "balloon number, nominal dimension value and unit, tolerance (bilateral/unilateral/limit/general), "
    "and any GD&T feature control frame (symbol, value, modifiers, datums). Only report balloons you "
    "can actually see in the image; never invent a balloon number. If a value is illegible, set it to "
    "null and lower your confidence for that field."
)

# Name of the tool Claude is forced to call so its reply is structured JSON rather than prose --
# see ClaudeChatClient.extract_balloons.
EXTRACTION_TOOL_NAME = "record_balloon_extraction"


def build_user_prompt(layout_text: str, page_number: int) -> str:
    return (
        f"Page {page_number}. Text extracted by layout analysis (for grounding, may be incomplete):\n"
        f"---\n{layout_text}\n---\n"
        f"Call the {EXTRACTION_TOOL_NAME} tool with every balloon on this page."
    )


class IChatCompletionClient(ABC):
    @abstractmethod
    def extract_balloons(
        self, *, image_b64: str, layout_text: str, page_number: int, json_schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Returns a dict matching BalloonExtractionResponse's schema (raises ClaudeApiError on
        transport/API failure; may return content that does NOT validate -- caller is responsible
        for schema validation/repair, see extraction_orchestrator.py)."""


class ClaudeChatClient(IChatCompletionClient):
    """Real implementation backed by Claude's Messages API (Anthropic Python SDK).

    Structured output is obtained via forced tool use rather than a `response_format` parameter
    (Claude has no direct equivalent) -- the extraction schema is registered as a single tool's
    `input_schema` and `tool_choice` forces Claude to call it, so `content` always contains a
    `tool_use` block whose `.input` is already a parsed dict (no json.loads needed, unlike the
    OpenAI-style `message.content` string this replaced).
    """

    def __init__(self, api_key: str, model: str, max_tokens: int = 4096, base_url: str | None = None):
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key, base_url=base_url) if base_url else Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    @external_api_retry((Exception,))
    def extract_balloons(
        self, *, image_b64: str, layout_text: str, page_number: int, json_schema: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=0,
                system=SYSTEM_PROMPT,
                tools=[
                    {
                        "name": EXTRACTION_TOOL_NAME,
                        "description": "Records the structured balloon extraction result for one drawing page.",
                        "input_schema": json_schema,
                    }
                ],
                tool_choice={"type": "tool", "name": EXTRACTION_TOOL_NAME},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": build_user_prompt(layout_text, page_number)},
                            {
                                "type": "image",
                                "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
                            },
                        ],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001 - normalize every SDK failure to our own error type
            raise ClaudeApiError(f"Claude extraction call failed: {exc}") from exc

        tool_use = next((block for block in response.content if getattr(block, "type", None) == "tool_use"), None)
        if tool_use is None:
            # Not a transport failure -- Claude replied but didn't call the forced tool (e.g. hit
            # max_tokens mid-call). Return an empty dict rather than raising: it fails
            # BalloonExtractionResponse schema validation the same way a malformed OpenAI response
            # used to, which routes it into the orchestrator's existing repair loop for free.
            logger.warning("Claude response on page %d had no tool_use block (stop_reason=%s)", page_number, response.stop_reason)
            return {}
        return tool_use.input


class FakeChatCompletionClient(IChatCompletionClient):
    """Test double: pops canned responses in order. Each entry is either a dict (returned as-is,
    valid or intentionally invalid for repair-loop testing) or an Exception (raised)."""

    def __init__(self, canned_responses: list[dict | Exception]):
        self._responses = list(canned_responses)
        self.calls: list[dict] = []

    def extract_balloons(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if not self._responses:
            raise ClaudeApiError("FakeChatCompletionClient: no more canned responses configured.")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def decode_image_b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")
