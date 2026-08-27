"""Adapter pattern (architecture-poc.md §2.2): thin wrappers around the Azure SDKs behind small
interfaces, so ExtractionOrchestrator / BalloonDetector never import the SDKs directly and unit
tests can substitute Fake* implementations with no network calls.
"""
from __future__ import annotations

import base64
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from src.exceptions import AzureOpenAIError, DocumentIntelligenceError
from src.retry import azure_retry

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

    @azure_retry((Exception,))
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
# Azure OpenAI
# --------------------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a mechanical engineering drawing analyst extracting dimension and tolerance data "
    "from a ballooned engineering drawing page. For every balloon visible on the page, return its "
    "balloon number, nominal dimension value and unit, tolerance (bilateral/unilateral/limit/general), "
    "and any GD&T feature control frame (symbol, value, modifiers, datums). Only report balloons you "
    "can actually see in the image; never invent a balloon number. If a value is illegible, set it to "
    "null and lower your confidence for that field."
)


def build_user_prompt(layout_text: str, page_number: int) -> str:
    return (
        f"Page {page_number}. Text extracted by layout analysis (for grounding, may be incomplete):\n"
        f"---\n{layout_text}\n---\n"
        "Return every balloon on this page as structured JSON matching the provided schema."
    )


class IChatCompletionClient(ABC):
    @abstractmethod
    def extract_balloons(
        self, *, image_b64: str, layout_text: str, page_number: int, json_schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Returns a dict matching BalloonExtractionResponse's schema (raises AzureOpenAIError on
        transport/API failure; may return content that does NOT validate -- caller is responsible
        for schema validation/repair, see extraction_orchestrator.py)."""


class AzureOpenAIChatClient(IChatCompletionClient):
    """Real implementation using Azure OpenAI structured outputs (response_format=json_schema)."""

    def __init__(self, endpoint: str, api_key: str, deployment: str, api_version: str):
        from openai import AzureOpenAI

        self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
        self._deployment = deployment

    @azure_retry((Exception,))
    def extract_balloons(
        self, *, image_b64: str, layout_text: str, page_number: int, json_schema: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            response = self._client.chat.completions.create(
                model=self._deployment,
                temperature=0,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "balloon_extraction", "schema": json_schema, "strict": True},
                },
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": build_user_prompt(layout_text, page_number)},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                        ],
                    },
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise AzureOpenAIError(f"Azure OpenAI extraction call failed: {exc}") from exc

        content = response.choices[0].message.content
        try:
            return json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AzureOpenAIError(f"Azure OpenAI returned non-JSON content: {exc}") from exc


class FakeChatCompletionClient(IChatCompletionClient):
    """Test double: pops canned responses in order. Each entry is either a dict (returned as-is,
    valid or intentionally invalid for repair-loop testing) or an Exception (raised)."""

    def __init__(self, canned_responses: list[dict | Exception]):
        self._responses = list(canned_responses)
        self.calls: list[dict] = []

    def extract_balloons(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if not self._responses:
            raise AzureOpenAIError("FakeChatCompletionClient: no more canned responses configured.")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def decode_image_b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")
