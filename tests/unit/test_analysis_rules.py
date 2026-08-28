"""Unit tests for the deterministic (non-AI) analysis checks -- src/analysis_rules.py. Pure
functions over plain models, no network, no mocking."""
from __future__ import annotations

from datetime import datetime, timezone

from src.analysis_rules import run_checks
from src.models import (
    AnalysisFindingCategory,
    AnalysisFindingSeverity,
    BalloonReviewRecord,
    BalloonReviewStatus,
    ExtractedBalloon,
    GdtInfo,
    ReconciliationRecord,
    ToleranceType,
)


def _balloon(number: int, page: int = 1, **overrides) -> ExtractedBalloon:
    defaults = dict(balloon_number=number, page=page, nominal_value=25.4, unit="mm", confidence=0.95)
    defaults.update(overrides)
    return ExtractedBalloon(**defaults)


def _record(*balloons_and_status: tuple[ExtractedBalloon, BalloonReviewStatus, ExtractedBalloon | None]) -> ReconciliationRecord:
    """Each arg is (extracted, status, reviewed_or_None)."""
    reviews = [
        BalloonReviewRecord(
            page=extracted.page,
            balloon_number=extracted.balloon_number,
            extracted=extracted,
            reviewed=reviewed,
            status=status,
            discrepancy=reviewed is not None and reviewed.model_dump() != extracted.model_dump(),
        )
        for extracted, status, reviewed in balloons_and_status
    ]
    return ReconciliationRecord(
        job_id="job-1", drawing_number="DWG-1", revision="A", balloons=reviews, created_at=datetime.now(timezone.utc)
    )


def _simple_record(*balloons: ExtractedBalloon) -> ReconciliationRecord:
    return _record(*[(b, BalloonReviewStatus.PENDING, None) for b in balloons])


def test_no_findings_for_a_clean_drawing():
    record = _simple_record(_balloon(1), _balloon(2, unit="mm"))

    findings = run_checks(record)

    assert findings == []


class TestMissingInformation:
    def test_flags_no_nominal_value_and_no_gdt(self):
        record = _simple_record(_balloon(1, nominal_value=None, gdt=None))

        [finding] = run_checks(record)

        assert finding.category == AnalysisFindingCategory.MISSING_INFO
        assert finding.severity == AnalysisFindingSeverity.WARNING
        assert (finding.balloon_refs[0].page, finding.balloon_refs[0].balloon_number) == (1, 1)

    def test_does_not_flag_missing_nominal_when_gdt_is_present(self):
        record = _simple_record(_balloon(1, nominal_value=None, gdt=GdtInfo(symbol="flatness", value=0.05)))

        findings = run_checks(record)

        assert not any(f.category == AnalysisFindingCategory.MISSING_INFO for f in findings)

    def test_flags_value_without_unit(self):
        record = _simple_record(_balloon(1, unit=None))

        findings = [f for f in run_checks(record) if f.category == AnalysisFindingCategory.MISSING_INFO]

        assert len(findings) == 1
        assert "no unit" in findings[0].summary

    def test_missing_info_severity_downgraded_once_reviewed(self):
        extracted = _balloon(1, nominal_value=None, gdt=None)
        record = _record((extracted, BalloonReviewStatus.RECONCILED, extracted))

        [finding] = run_checks(record)

        assert finding.severity == AnalysisFindingSeverity.INFO

    def test_does_not_double_report_a_balloon_that_already_failed_extraction(self):
        record = _simple_record(_balloon(1, nominal_value=None, gdt=None, extraction_error="model returned malformed JSON"))

        findings = run_checks(record)

        assert not any(f.category == AnalysisFindingCategory.MISSING_INFO for f in findings)
        assert any(f.category == AnalysisFindingCategory.INCOMPLETE_DATA for f in findings)


class TestIncompleteData:
    def test_flags_extraction_error(self):
        record = _simple_record(_balloon(1, extraction_error="repair attempt failed"))

        [finding] = run_checks(record)

        assert finding.category == AnalysisFindingCategory.INCOMPLETE_DATA
        assert finding.severity == AnalysisFindingSeverity.CRITICAL

    def test_flags_blocked_balloon(self):
        record = _simple_record(_balloon(1, blocked=True))

        [finding] = run_checks(record)

        assert finding.category == AnalysisFindingCategory.INCOMPLETE_DATA
        assert finding.severity == AnalysisFindingSeverity.CRITICAL

    def test_flags_low_confidence(self):
        record = _simple_record(_balloon(1, confidence=0.4))

        [finding] = run_checks(record)

        assert finding.category == AnalysisFindingCategory.INCOMPLETE_DATA
        assert finding.severity == AnalysisFindingSeverity.WARNING

    def test_does_not_flag_confidence_at_or_above_threshold(self):
        record = _simple_record(_balloon(1, confidence=0.7))

        findings = run_checks(record)

        assert not any(f.category == AnalysisFindingCategory.INCOMPLETE_DATA for f in findings)

    def test_incomplete_data_checked_against_extracted_not_reviewed_confidence(self):
        """A reviewer's correction doesn't retroactively make the original extraction reliable --
        the finding should still surface (just downgraded to INFO since it's been reviewed)."""
        extracted = _balloon(1, confidence=0.3)
        reviewed = _balloon(1, confidence=0.3, nominal_value=30.0)  # reviewer corrected the value
        record = _record((extracted, BalloonReviewStatus.RECONCILED, reviewed))

        [finding] = run_checks(record)

        assert finding.category == AnalysisFindingCategory.INCOMPLETE_DATA
        assert finding.severity == AnalysisFindingSeverity.INFO


class TestSheetInconsistencies:
    def test_flags_duplicate_balloon_number_on_the_same_page(self):
        record = _simple_record(_balloon(1, page=1, nominal_value=10.0), _balloon(1, page=1, nominal_value=12.0))

        [finding] = run_checks(record)

        assert finding.category == AnalysisFindingCategory.INCONSISTENCY
        assert finding.severity == AnalysisFindingSeverity.CRITICAL
        assert len(finding.balloon_refs) == 2

    def test_flags_disagreement_across_sheets(self):
        record = _simple_record(_balloon(5, page=1, nominal_value=10.0), _balloon(5, page=2, nominal_value=12.0))

        [finding] = run_checks(record)

        assert finding.category == AnalysisFindingCategory.INCONSISTENCY
        assert finding.severity == AnalysisFindingSeverity.WARNING
        assert "sheets" in finding.summary

    def test_does_not_flag_the_same_balloon_number_on_different_sheets_with_matching_data(self):
        record = _simple_record(_balloon(5, page=1, nominal_value=10.0), _balloon(5, page=2, nominal_value=10.0))

        findings = run_checks(record)

        assert findings == []


class TestCommonMistakes:
    def test_flags_datum_requiring_gdt_symbol_with_no_datums(self):
        record = _simple_record(_balloon(1, gdt=GdtInfo(symbol="position", value=0.1, datums=[])))

        findings = [f for f in run_checks(record) if f.category == AnalysisFindingCategory.COMMON_MISTAKE]

        assert len(findings) == 1
        assert "no datum" in findings[0].summary

    def test_does_not_flag_gdt_symbol_that_does_not_require_a_datum(self):
        record = _simple_record(_balloon(1, gdt=GdtInfo(symbol="flatness", value=0.05, datums=[])))

        findings = [f for f in run_checks(record) if f.category == AnalysisFindingCategory.COMMON_MISTAKE]

        assert findings == []

    def test_does_not_flag_datum_requiring_symbol_when_datums_present(self):
        record = _simple_record(_balloon(1, gdt=GdtInfo(symbol="position", value=0.1, datums=["A", "B"])))

        findings = [f for f in run_checks(record) if f.category == AnalysisFindingCategory.COMMON_MISTAKE]

        assert findings == []

    def test_flags_tolerance_type_with_no_tolerance_values(self):
        record = _simple_record(
            _balloon(1, tolerance_type=ToleranceType.BILATERAL, upper_tol=None, lower_tol=None)
        )

        findings = [f for f in run_checks(record) if f.category == AnalysisFindingCategory.COMMON_MISTAKE]

        assert len(findings) == 1
        assert "no tolerance values" in findings[0].summary

    def test_does_not_flag_tolerance_type_when_values_present(self):
        record = _simple_record(
            _balloon(1, tolerance_type=ToleranceType.BILATERAL, upper_tol=0.05, lower_tol=-0.05)
        )

        findings = [f for f in run_checks(record) if f.category == AnalysisFindingCategory.COMMON_MISTAKE]

        assert findings == []

    def test_flags_mixed_units_across_the_drawing(self):
        record = _simple_record(
            _balloon(1, unit="mm"), _balloon(2, unit="mm"), _balloon(3, unit="mm"), _balloon(4, unit="in")
        )

        findings = [f for f in run_checks(record) if f.category == AnalysisFindingCategory.COMMON_MISTAKE]
        mixed = [f for f in findings if "Mixed units" in f.summary]

        assert len(mixed) == 1
        assert len(mixed[0].balloon_refs) == 1  # the one minority-unit balloon

    def test_does_not_flag_a_single_consistent_unit(self):
        record = _simple_record(_balloon(1, unit="mm"), _balloon(2, unit="mm"))

        findings = [f for f in run_checks(record) if "Mixed units" in f.summary]

        assert findings == []
