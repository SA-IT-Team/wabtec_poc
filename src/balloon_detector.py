"""BalloonDetector: calls Document Intelligence layout analysis and isolates short, isolated
numeric tokens as balloon candidates.

POC heuristic, explicitly: Document Intelligence's prebuilt-layout model does not directly expose
"this is a circled number" as a primitive, so this POC treats any 1-3 digit numeric token (with or
without surrounding parentheses, a common OCR rendering of a circled digit) as a candidate. This is
the simplification the POC exists to stress-test; architecture-full.md's production path is a
custom-trained shape-detection model if this heuristic's false-positive/negative rate proves too high.
"""
from __future__ import annotations

import re
from typing import Any

from src.ai_clients import IDocumentAnalysisClient
from src.exceptions import DocumentIntelligenceError
from src.preprocessor import PageImage

BALLOON_NUMBER_PATTERN = re.compile(r"^\(?(\d{1,3})\)?$")


class BalloonDetector:
    def __init__(self, client: IDocumentAnalysisClient):
        self._client = client

    def detect(self, page: PageImage) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Returns (balloon_candidates, raw_layout_dict). Candidates are plain dicts:
        {balloon_number, page, bounding_box, confidence}."""
        try:
            layout = self._client.analyze_layout(page.png_bytes)
        except DocumentIntelligenceError:
            raise
        except Exception as exc:  # noqa: BLE001 - defensive: fakes/tests may raise arbitrary errors
            raise DocumentIntelligenceError(str(exc)) from exc

        return self._extract_candidates(layout, page.page_number), layout

    @staticmethod
    def _extract_candidates(layout: dict[str, Any], page_number: int) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen_numbers: set[int] = set()
        for word in layout.get("words", []):
            text = (word.get("content") or "").strip()
            match = BALLOON_NUMBER_PATTERN.match(text)
            if not match:
                continue
            number = int(match.group(1))
            if number in seen_numbers:
                continue  # duplicate token for the same number on this page; keep the first
            seen_numbers.add(number)
            candidates.append(
                {
                    "balloon_number": number,
                    "page": page_number,
                    "bounding_box": word.get("polygon"),
                    "confidence": word.get("confidence", 0.5),
                }
            )
        return sorted(candidates, key=lambda c: c["balloon_number"])
