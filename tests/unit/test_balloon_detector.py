import pytest

from src.ai_clients import FakeDocumentAnalysisClient
from src.balloon_detector import BalloonDetector
from src.exceptions import DocumentIntelligenceError
from src.preprocessor import PageImage


def _page() -> PageImage:
    return PageImage(page_number=1, png_bytes=b"fake-png", dpi=300, width_px=100, height_px=100)


def test_extracts_numeric_balloon_candidates(sample_layout):
    detector = BalloonDetector(FakeDocumentAnalysisClient(sample_layout))

    candidates, layout = detector.detect(_page())

    assert [c["balloon_number"] for c in candidates] == [12, 13]
    assert layout == sample_layout


def test_ignores_non_numeric_and_long_tokens():
    layout = {
        "lines": [],
        "words": [
            {"content": "ABC", "polygon": None, "confidence": 0.9},
            {"content": "12345", "polygon": None, "confidence": 0.9},  # too long to be a balloon
            {"content": "(7)", "polygon": None, "confidence": 0.9},  # parenthesized is valid
        ],
    }
    detector = BalloonDetector(FakeDocumentAnalysisClient(layout))

    candidates, _ = detector.detect(_page())

    assert [c["balloon_number"] for c in candidates] == [7]


def test_deduplicates_repeated_balloon_numbers():
    layout = {"lines": [], "words": [{"content": "5", "confidence": 0.9}, {"content": "5", "confidence": 0.8}]}
    detector = BalloonDetector(FakeDocumentAnalysisClient(layout))

    candidates, _ = detector.detect(_page())

    assert len(candidates) == 1


def test_wraps_upstream_failure_as_document_intelligence_error():
    detector = BalloonDetector(FakeDocumentAnalysisClient(RuntimeError("service unavailable")))

    with pytest.raises(DocumentIntelligenceError):
        detector.detect(_page())
