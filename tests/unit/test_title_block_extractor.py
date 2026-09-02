from src.title_block_extractor import TitleBlockExtractor


def _layout(*line_contents: str) -> dict:
    return {"lines": [{"content": c} for c in line_contents], "words": []}


def test_returns_none_for_both_fields_when_nothing_matches():
    extractor = TitleBlockExtractor()
    fields = extractor.extract(_layout("SOME RANDOM NOTE", "25.4 ±0.05", "MATERIAL: 1060 ALLOY"))

    assert fields.drawing_number is None
    assert fields.revision is None


def test_empty_layout_returns_none_for_both():
    extractor = TitleBlockExtractor()
    fields = extractor.extract({"lines": [], "words": []})

    assert fields.drawing_number is None
    assert fields.revision is None


class TestSizeDwgRevRow:
    """The standard ASME/ANSI sheet-format corner -- SolidWorks' and most mechanical CAD tools'
    default title block, exactly the layout that surfaced this gap in the first place."""

    def test_reads_both_fields_from_the_header_and_value_row(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("TITLE", "Counterweight Wheel", "SIZE DWG. NO. REV", "B CW-2045 A"))

        assert fields.drawing_number == "CW-2045"
        assert fields.revision == "A"

    def test_case_insensitive_and_tolerates_missing_periods(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("size dwg no rev", "B CW-2045 A"))

        assert fields.drawing_number == "CW-2045"
        assert fields.revision == "A"

    def test_ignored_when_value_row_does_not_have_exactly_three_tokens(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("SIZE DWG. NO. REV", "B CW-2045"))  # missing the REV value

        assert fields.drawing_number is None
        assert fields.revision is None

    def test_ignored_when_header_row_has_extra_text(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("PAGE SIZE DWG. NO. REV LETTER", "B CW-2045 A"))

        assert fields.drawing_number is None
        assert fields.revision is None

    def test_reads_both_fields_when_every_header_and_value_cell_is_its_own_line(self):
        """The layout actually reported from a real drawing: Document Intelligence OCR'd each
        title-block cell as its own line rather than grouping a row into one combined line --
        SIZE, DWG. NO., and REV as three separate label lines, then B, CW-2045, and A as three
        separate value lines right after. Regression test for reading "REV" itself back as the
        drawing number when DWG. NO. and REV land on adjacent lines with no value between them."""
        extractor = TitleBlockExtractor()
        fields = extractor.extract(
            _layout(
                "TITLE",
                "Counterweight Wheel",
                "SIZE",
                "DWG. NO.",
                "REV",
                "B",
                "CW-2045",
                "A",
                "SCALE: 1:1  WEIGHT: 604.55  SHEET 1 OF 1",
            )
        )

        assert fields.drawing_number == "CW-2045"
        assert fields.revision == "A"

    def test_separate_cell_lines_variant_also_respects_missing_values(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("SIZE", "DWG. NO.", "REV", "B", "CW-2045"))  # REV's value never arrives

        assert fields.drawing_number is None  # not enough tokens to safely assign by position
        assert fields.revision is None


class TestInlineLabelAndValue:
    def test_dwg_no_with_colon(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("DWG NO: CW-2045"))
        assert fields.drawing_number == "CW-2045"

    def test_dwg_no_with_periods_no_separator(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("DWG. NO. CW-2045"))
        assert fields.drawing_number == "CW-2045"

    def test_drawing_no_spelled_out(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("DRAWING NO. 10245-B"))
        assert fields.drawing_number == "10245-B"

    def test_drawing_number_fully_spelled_out(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("DRAWING NUMBER", "ABT-W001"))
        assert fields.drawing_number == "ABT-W001"

    def test_part_number_spelled_out(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("PART NUMBER CW-2045"))
        assert fields.drawing_number == "CW-2045"

    def test_rev_with_period(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("REV. A"))
        assert fields.revision == "A"

    def test_revision_spelled_out_with_colon(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("REVISION: B"))
        assert fields.revision == "B"

    def test_both_labels_on_one_combined_line(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("DWG. NO. CW-2045 REV A"))
        assert fields.drawing_number == "CW-2045"
        assert fields.revision == "A"

    def test_does_not_false_positive_on_revision_history_heading(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("REVISION HISTORY", "A", "Initial release"))
        assert fields.revision is None


class TestLabelOnlyLineThenValueLine:
    def test_dwg_no_label_alone_then_value_on_next_line(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("DWG. NO.", "CW-2045"))
        assert fields.drawing_number == "CW-2045"

    def test_rev_label_alone_then_value_on_next_line(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("REV", "A"))
        assert fields.revision == "A"

    def test_label_alone_with_nothing_useful_on_the_next_line_yields_none(self):
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("REV", "DESCRIPTION"))  # a revision-history table header, not a value
        assert fields.revision is None

    def test_does_not_read_an_adjacent_label_as_the_value(self):
        """DWG. NO. immediately followed by REV, with nothing between them, outside of a
        recognized SIZE/DWG.NO./REV triplet -- must not read "REV" back as the drawing number."""
        extractor = TitleBlockExtractor()
        fields = extractor.extract(_layout("DWG. NO.", "REV", "CW-2045"))
        assert fields.drawing_number is None
