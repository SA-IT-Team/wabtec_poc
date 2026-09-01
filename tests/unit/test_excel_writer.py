import io

import pytest
from openpyxl import load_workbook

from src.excel_templates import AS9102_FORM3, GENERIC_FLAT
from src.excel_writer import EXTRACTED_SHEET_NAME, LOW_CONFIDENCE_THRESHOLD, RECONCILED_SHEET_NAME, ExcelWriter
from src.exceptions import UnknownTemplateError
from src.models import ExtractedBalloon, GdtInfo, ToleranceType


def test_defaults_to_the_as9102_template_when_none_is_given():
    writer = ExcelWriter()
    xlsx_bytes = writer.write(drawing_number="DWG-1", revision="A", reconciled_balloons=[])

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    assert wb.active.title == RECONCILED_SHEET_NAME
    # AS9102's title block is what's on the sheet -- confirms the default template was used
    assert wb.active.cell(row=1, column=1).value == "Drawing / Part No."


def test_rejects_an_unknown_template_id():
    writer = ExcelWriter()
    with pytest.raises(UnknownTemplateError, match="bogus-template"):
        writer.write(drawing_number="DWG-1", revision="A", reconciled_balloons=[], template_id="bogus-template")


def test_single_tab_when_extracted_balloons_is_not_given():
    writer = ExcelWriter()
    xlsx_bytes = writer.write(drawing_number="DWG-1", revision="A", reconciled_balloons=[])

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    assert wb.sheetnames == [RECONCILED_SHEET_NAME]


class TestDualTabExport:
    def test_both_tabs_present_with_reconciled_active(self):
        writer = ExcelWriter()
        xlsx_bytes = writer.write(
            drawing_number="DWG-1", revision="A", reconciled_balloons=[], extracted_balloons=[], template_id="generic-flat"
        )

        wb = load_workbook(io.BytesIO(xlsx_bytes))
        assert wb.sheetnames == [RECONCILED_SHEET_NAME, EXTRACTED_SHEET_NAME]
        assert wb.active.title == RECONCILED_SHEET_NAME

    def test_each_tab_shows_its_own_data(self):
        writer = ExcelWriter()
        reconciled = [ExtractedBalloon(balloon_number=1, nominal_value=25.5, confidence=1.0, notes="reviewer-corrected")]
        extracted = [ExtractedBalloon(balloon_number=1, nominal_value=25.4, confidence=0.6)]

        xlsx_bytes = writer.write(
            drawing_number="DWG-1",
            revision="A",
            reconciled_balloons=reconciled,
            extracted_balloons=extracted,
            template_id="generic-flat",
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))

        nominal_col = [c for c, (key, _) in enumerate(GENERIC_FLAT.columns, start=1) if key == "nominal_value"][0]
        assert wb[RECONCILED_SHEET_NAME].cell(row=2, column=nominal_col).value == 25.5
        assert wb[EXTRACTED_SHEET_NAME].cell(row=2, column=nominal_col).value == 25.4

    def test_low_confidence_highlighting_is_independent_per_tab(self):
        writer = ExcelWriter()
        reconciled = [ExtractedBalloon(balloon_number=1, nominal_value=25.5, confidence=1.0)]  # human-verified, not highlighted
        extracted = [ExtractedBalloon(balloon_number=1, nominal_value=25.4, confidence=0.3)]  # low confidence, highlighted

        xlsx_bytes = writer.write(
            drawing_number="DWG-1",
            revision="A",
            reconciled_balloons=reconciled,
            extracted_balloons=extracted,
            template_id="generic-flat",
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))

        default_fill = wb[RECONCILED_SHEET_NAME].cell(row=2, column=1).fill.start_color.rgb
        low_confidence_fill = wb[EXTRACTED_SHEET_NAME].cell(row=2, column=1).fill.start_color.rgb
        assert default_fill != low_confidence_fill

    def test_both_tabs_use_the_same_template_layout(self):
        writer = ExcelWriter()
        xlsx_bytes = writer.write(
            drawing_number="DWG-10245",
            revision="C",
            reconciled_balloons=[],
            extracted_balloons=[],
            template_id="as9102-form3",
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))

        for sheet_name in (RECONCILED_SHEET_NAME, EXTRACTED_SHEET_NAME):
            ws = wb[sheet_name]
            assert ws.cell(row=1, column=1).value == "Drawing / Part No."
            assert ws.cell(row=1, column=2).value == "DWG-10245"
            header_row = [c.value for c in next(ws.iter_rows(min_row=5, max_row=5))]
            assert header_row == [h for _, h in AS9102_FORM3.columns]


class TestGenericFlatTemplate:
    def test_writes_header_row_matching_template_columns(self):
        writer = ExcelWriter()
        xlsx_bytes = writer.write(drawing_number="DWG-1", revision="A", reconciled_balloons=[], template_id="generic-flat")

        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        header = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
        assert header == [h for _, h in GENERIC_FLAT.columns]
        assert ws.title == RECONCILED_SHEET_NAME

    def test_writes_one_row_per_balloon_sorted_by_number(self):
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

        xlsx_bytes = writer.write(
            drawing_number="DWG-1", revision="A", reconciled_balloons=balloons, template_id="generic-flat"
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        balloon_number_col = [c for c, (key, _) in enumerate(GENERIC_FLAT.columns, start=1) if key == "balloon_number"][0]
        values = [row[balloon_number_col - 1].value for row in ws.iter_rows(min_row=2, max_row=3)]
        assert values == [1, 5]  # sorted ascending, not insertion order

    def test_low_confidence_rows_are_highlighted(self):
        writer = ExcelWriter()
        balloons = [
            ExtractedBalloon(balloon_number=1, nominal_value=1.0, confidence=0.95),  # not highlighted
            ExtractedBalloon(balloon_number=2, nominal_value=1.0, confidence=0.4),  # highlighted
        ]

        xlsx_bytes = writer.write(
            drawing_number="DWG-1", revision="A", reconciled_balloons=balloons, template_id="generic-flat"
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        assert balloons[1].confidence < LOW_CONFIDENCE_THRESHOLD
        row2_fill = ws.cell(row=2, column=1).fill.start_color.rgb
        row3_fill = ws.cell(row=3, column=1).fill.start_color.rgb
        assert row2_fill != row3_fill  # the low-confidence row's fill differs from the normal row's

    def test_extraction_error_is_surfaced_in_notes(self):
        writer = ExcelWriter()
        balloons = [
            ExtractedBalloon(
                balloon_number=9,
                confidence=0.0,
                extraction_error="Detected by layout analysis but not returned by the extraction model.",
            )
        ]

        xlsx_bytes = writer.write(
            drawing_number="DWG-1", revision="A", reconciled_balloons=balloons, template_id="generic-flat"
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        notes_col = [c for c, (key, _) in enumerate(GENERIC_FLAT.columns, start=1) if key == "notes"][0]
        assert "ERROR" in ws.cell(row=2, column=notes_col).value

    def test_confidence_reason_is_surfaced_in_notes(self):
        writer = ExcelWriter()
        balloons = [
            ExtractedBalloon(
                balloon_number=1,
                nominal_value=25.4,
                confidence=0.4,
                notes="reviewer flagged for a closer look",
                confidence_reason="Digit partly obscured by a fold in the scan.",
            )
        ]

        xlsx_bytes = writer.write(
            drawing_number="DWG-1", revision="A", reconciled_balloons=balloons, template_id="generic-flat"
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        notes_col = [c for c, (key, _) in enumerate(GENERIC_FLAT.columns, start=1) if key == "notes"][0]
        notes_value = ws.cell(row=2, column=notes_col).value
        assert "reviewer flagged for a closer look" in notes_value
        assert "confidence: Digit partly obscured by a fold in the scan." in notes_value
        # own notes come before the confidence reasoning, in that order
        assert notes_value.index("reviewer flagged") < notes_value.index("confidence:")

    def test_no_title_block(self):
        writer = ExcelWriter()
        xlsx_bytes = writer.write(drawing_number="DWG-1", revision="A", reconciled_balloons=[], template_id="generic-flat")
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active
        # header row is row 1 -- no title block rows pushed it down
        assert [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))][0] == "Balloon #"


class TestAs9102Form3Template:
    def test_renders_a_title_block_above_the_table(self):
        writer = ExcelWriter()
        xlsx_bytes = writer.write(
            drawing_number="DWG-10245", revision="C", reconciled_balloons=[], template_id="as9102-form3"
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        assert ws.cell(row=1, column=1).value == "Drawing / Part No."
        assert ws.cell(row=1, column=2).value == "DWG-10245"
        assert ws.cell(row=2, column=1).value == "Revision"
        assert ws.cell(row=2, column=2).value == "C"

        header_row = [c.value for c in next(ws.iter_rows(min_row=5, max_row=5))]
        assert header_row == [h for _, h in AS9102_FORM3.columns]

    def test_composes_the_requirement_column_from_nominal_and_tolerance(self):
        writer = ExcelWriter()
        balloon = ExtractedBalloon(
            balloon_number=1, nominal_value=25.4, unit="mm", upper_tol=0.05, lower_tol=-0.05, confidence=0.9
        )
        xlsx_bytes = writer.write(
            drawing_number="DWG-1", revision="A", reconciled_balloons=[balloon], template_id="as9102-form3"
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        req_col = [c for c, (key, _) in enumerate(AS9102_FORM3.columns, start=1) if key == "requirement"][0]
        value = ws.cell(row=6, column=req_col).value  # 5 title-block/header rows, first data row is 6
        assert "25.4 mm" in value
        assert "+0.05/-0.05" in value

    def test_composes_the_requirement_column_from_a_gdt_frame(self):
        writer = ExcelWriter()
        balloon = ExtractedBalloon(
            balloon_number=2,
            confidence=0.9,
            gdt=GdtInfo(symbol="position", value=0.1, modifiers=["MMC"], datums=["A", "B"]),
        )
        xlsx_bytes = writer.write(
            drawing_number="DWG-1", revision="A", reconciled_balloons=[balloon], template_id="as9102-form3"
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        req_col = [c for c, (key, _) in enumerate(AS9102_FORM3.columns, start=1) if key == "requirement"][0]
        value = ws.cell(row=6, column=req_col).value
        assert "position 0.1" in value
        assert "MMC" in value
        assert "A-B" in value

    def test_falls_back_to_an_em_dash_when_neither_value_nor_gdt_is_present(self):
        writer = ExcelWriter()
        balloon = ExtractedBalloon(balloon_number=3, confidence=0.0, extraction_error="not returned by the model")
        xlsx_bytes = writer.write(
            drawing_number="DWG-1", revision="A", reconciled_balloons=[balloon], template_id="as9102-form3"
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        req_col = [c for c, (key, _) in enumerate(AS9102_FORM3.columns, start=1) if key == "requirement"][0]
        assert ws.cell(row=6, column=req_col).value == "—"

    def test_uses_bilateral_tolerance_type_when_no_explicit_values_present(self):
        writer = ExcelWriter()
        balloon = ExtractedBalloon(
            balloon_number=4, nominal_value=10.0, unit="mm", tolerance_type=ToleranceType.GENERAL, confidence=0.9
        )
        xlsx_bytes = writer.write(
            drawing_number="DWG-1", revision="A", reconciled_balloons=[balloon], template_id="as9102-form3"
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        req_col = [c for c, (key, _) in enumerate(AS9102_FORM3.columns, start=1) if key == "requirement"][0]
        assert "[general]" in ws.cell(row=6, column=req_col).value

    def test_confidence_reason_is_surfaced_in_notes(self):
        writer = ExcelWriter()
        balloon = ExtractedBalloon(
            balloon_number=5, nominal_value=1.0, confidence=0.5, confidence_reason="Leader line overlaps an adjacent callout."
        )
        xlsx_bytes = writer.write(
            drawing_number="DWG-1", revision="A", reconciled_balloons=[balloon], template_id="as9102-form3"
        )
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        notes_col = [c for c, (key, _) in enumerate(AS9102_FORM3.columns, start=1) if key == "notes"][0]
        assert "confidence: Leader line overlaps an adjacent callout." in ws.cell(row=6, column=notes_col).value
