"""Shared pytest fixtures: synthetic PDFs/images, fixture JSON loaders, and factory helpers so
individual tests stay short and focused on behavior rather than test-data plumbing.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pymupdf as fitz  # PyMuPDF -- `pymupdf` is the current import name; `fitz` is deprecated
import pytest
from PIL import Image

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_layout() -> dict:
    return load_fixture("sample_layout_result.json")


@pytest.fixture
def sample_extraction_response() -> dict:
    return load_fixture("sample_extraction_response.json")


@pytest.fixture
def make_pdf_bytes():
    """Factory fixture: make_pdf_bytes(page_count=3) -> bytes of a minimal blank PDF."""

    def _make(page_count: int = 1) -> bytes:
        doc = fitz.open()
        for _ in range(page_count):
            doc.new_page(width=612, height=792)  # US Letter at 72 DPI
        return doc.tobytes()

    return _make


@pytest.fixture
def make_image_bytes():
    """Factory fixture: make_image_bytes(dpi=300) -> PNG bytes at the given DPI metadata."""

    def _make(dpi: int = 300, size: tuple[int, int] = (200, 200)) -> bytes:
        img = Image.new("RGB", size, color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG", dpi=(dpi, dpi))
        return buf.getvalue()

    return _make
