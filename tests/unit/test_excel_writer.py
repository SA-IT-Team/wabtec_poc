import io

from openpyxl import load_workbook

from src.excel_writer import DEFAULT_TEMPLATE_COLUMNS, LOW_CONFIDENCE_THRESHOLD, ExcelWriter
from src.models import ExtractedBalloon, GdtInfo, ToleranceType


def test_writes_header_row_matching_template_columns():
    writer = ExcelWriter()
    xlsx_bytes = writer.write(drawing_number="DWG-1", revision="A", balloons=[])

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb.active

    header = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    assert header == [h for _, h in DEFAULT_TEMPLATE_COLUMNS]


def test_writes_one_row_per_balloon_sorted_by_number():
    writer = ExcelWriter()
    balloons = [
        ExtractedBalloon(balloon_number=5, nominal_value=1.0, confidence=0.9),
        ExtractedBalloon(
            balloon_number=1,
            nominal_value=2.0,
            confidence=0.9,
            gdt=GdtInfo(symbol="position", value=0.1, modifiers=["MMC"], datums=["A"]),
        ),
    ]

    xlsx_bytes = writer.write(drawing_number="DWG-1", revision="A", balloons=balloons)
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb.active

    balloon_number_col = [c for c, (key, _) in enumerate(DEFAULT_TEMPLATE_COLUMNS, start=1) if key == "balloon_number"][0]
    values = [row[balloon_number_col - 1].value for row in ws.iter_rows(min_row=2, max_row=3)]
    assert values == [1, 5]  # sorted ascending, not insertion order


def test_low_confidence_rows_are_highlighted():
    writer = ExcelWriter()
    balloons = [
        ExtractedBalloon(balloon_number=1, nominal_value=1.0, confidence=0.95),  # not highlighted
        ExtractedBalloon(balloon_number=2, nominal_value=1.0, confidence=0.4),  # highlighted
    ]

    xlsx_bytes = writer.write(drawing_number="DWG-1", revision="A", balloons=balloons)
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb.active

    assert balloons[1].confidence < LOW_CONFIDENCE_THRESHOLD
    row2_fill = ws.cell(row=2, column=1).fill.start_color.rgb
    row3_fill = ws.cell(row=3, column=1).fill.start_color.rgb
    assert row2_fill != row3_fill  # the low-confidence row's fill differs from the normal row's


def test_extraction_error_is_surfaced_in_notes():
    writer = ExcelWriter()
    balloons = [
        ExtractedBalloon(
            balloon_number=9,
            confidence=0.0,
            extraction_error="Detected by layout analysis but not returned by the extraction model.",
        )
    ]

    xlsx_bytes = writer.write(drawing_number="DWG-1", revision="A", balloons=balloons)
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb.active

    notes_col = [c for c, (key, _) in enumerate(DEFAULT_TEMPLATE_COLUMNS, start=1) if key == "notes"][0]
    assert "ERROR" in ws.cell(row=2, column=notes_col).value
