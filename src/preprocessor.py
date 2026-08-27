"""DrawingPreprocessor: converts PDF pages to raster images at a controlled DPI and enforces the
quality/page-count floors called out in architecture-poc.md §2.1 / FR-03.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import pymupdf as fitz  # PyMuPDF -- `pymupdf` is the current import name; `fitz` is deprecated
from PIL import Image

from src.exceptions import PageLimitExceededError, QualityThresholdError, UnsupportedFileTypeError

SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/tiff"}


@dataclass
class PageImage:
    page_number: int
    png_bytes: bytes
    dpi: int
    width_px: int
    height_px: int


class DrawingPreprocessor:
    def __init__(self, target_dpi: int = 300, min_dpi: int = 200, max_pages: int = 10):
        self.target_dpi = target_dpi
        self.min_dpi = min_dpi
        self.max_pages = max_pages

    def process(self, file_bytes: bytes, content_type: str) -> list[PageImage]:
        if content_type == "application/pdf":
            return self._rasterize_pdf(file_bytes)
        if content_type in SUPPORTED_IMAGE_TYPES:
            return [self._process_single_image(file_bytes)]
        raise UnsupportedFileTypeError(
            f"Unsupported content type '{content_type}'. Supported: application/pdf, "
            f"{', '.join(sorted(SUPPORTED_IMAGE_TYPES))}."
        )

    def _rasterize_pdf(self, file_bytes: bytes) -> list[PageImage]:
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise UnsupportedFileTypeError(f"Could not open file as PDF: {exc}") from exc

        if doc.page_count == 0:
            raise UnsupportedFileTypeError("PDF has no pages.")
        if doc.page_count > self.max_pages:
            raise PageLimitExceededError(
                f"PDF has {doc.page_count} pages; this endpoint accepts at most {self.max_pages} "
                "per request (architecture-poc.md §2.3)."
            )

        zoom = self.target_dpi / 72  # PDF default is 72 DPI
        matrix = fitz.Matrix(zoom, zoom)
        pages: list[PageImage] = []
        for index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix)
            pages.append(
                PageImage(
                    page_number=index + 1,
                    png_bytes=pix.tobytes("png"),
                    dpi=self.target_dpi,
                    width_px=pix.width,
                    height_px=pix.height,
                )
            )
        return pages

    def _process_single_image(self, file_bytes: bytes) -> PageImage:
        try:
            img = Image.open(io.BytesIO(file_bytes))
            img.load()
        except Exception as exc:  # noqa: BLE001
            raise UnsupportedFileTypeError(f"Could not open file as an image: {exc}") from exc

        dpi_info = img.info.get("dpi")
        dpi = int(dpi_info[0]) if dpi_info else self.target_dpi
        if dpi < self.min_dpi:
            raise QualityThresholdError(
                f"Image resolution {dpi} DPI is below the minimum of {self.min_dpi} DPI (FR-03)."
            )

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return PageImage(page_number=1, png_bytes=buf.getvalue(), dpi=dpi, width_px=img.width, height_px=img.height)
