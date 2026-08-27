import pytest

from src.exceptions import PageLimitExceededError, QualityThresholdError, UnsupportedFileTypeError
from src.preprocessor import DrawingPreprocessor


def test_rejects_unsupported_content_type(make_pdf_bytes):
    preprocessor = DrawingPreprocessor()
    with pytest.raises(UnsupportedFileTypeError):
        preprocessor.process(b"not a real file", "application/zip")


def test_rasterizes_pdf_pages_at_target_dpi(make_pdf_bytes):
    preprocessor = DrawingPreprocessor(target_dpi=150, max_pages=10)
    pages = preprocessor.process(make_pdf_bytes(page_count=2), "application/pdf")

    assert len(pages) == 2
    assert [p.page_number for p in pages] == [1, 2]
    assert all(p.dpi == 150 for p in pages)
    assert all(p.png_bytes.startswith(b"\x89PNG") for p in pages)


def test_pdf_over_page_limit_raises(make_pdf_bytes):
    preprocessor = DrawingPreprocessor(max_pages=5)
    with pytest.raises(PageLimitExceededError):
        preprocessor.process(make_pdf_bytes(page_count=6), "application/pdf")


def test_image_below_min_dpi_raises(make_image_bytes):
    preprocessor = DrawingPreprocessor(min_dpi=200)
    with pytest.raises(QualityThresholdError):
        preprocessor.process(make_image_bytes(dpi=72), "image/png")


def test_image_at_or_above_min_dpi_succeeds(make_image_bytes):
    preprocessor = DrawingPreprocessor(min_dpi=200)
    pages = preprocessor.process(make_image_bytes(dpi=300), "image/png")

    assert len(pages) == 1
    # PNG stores DPI as pixels-per-meter, so a round trip through PIL is lossy by a rounding
    # unit (300 DPI in -> 299 DPI out is expected) -- assert "close enough", not exact equality.
    assert abs(pages[0].dpi - 300) <= 1
    assert pages[0].page_number == 1
