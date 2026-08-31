"""Unit tests for ReconciliationService -- the human quality-check workflow (requirements.md's
"perform a quality check/reconciliation to ensure 100% data accuracy" item). All against
InMemoryReconciliationStore -- no network, pure business-rule testing.
"""
from __future__ import annotations

import pytest

from src.exceptions import (
    BalloonNotFoundError,
    IncompleteReconciliationError,
    JobNotFoundError,
    SegregationOfDutiesError,
    ValidationError,
)
from src.models import BalloonReviewStatus, ExtractedBalloon, ReviewAction
from src.reconciliation import ReconciliationService
from src.reconciliation_store import InMemoryReconciliationStore


def _balloon(number: int, page: int = 1, nominal: float = 25.4) -> ExtractedBalloon:
    return ExtractedBalloon(balloon_number=number, page=page, nominal_value=nominal, unit="mm", confidence=0.9)


@pytest.fixture
def service() -> ReconciliationService:
    return ReconciliationService(InMemoryReconciliationStore())


def test_start_seeds_every_balloon_as_pending(service):
    record = service.start("job-1", "DWG-1", "A", [_balloon(1), _balloon(2)], submitted_by="alice")

    assert len(record.balloons) == 2
    assert all(b.status == BalloonReviewStatus.PENDING for b in record.balloons)
    assert record.signed_off is False


def test_start_remembers_the_template_id_chosen_at_upload(service):
    record = service.start("job-1", "DWG-1", "A", [_balloon(1)], template_id="generic-flat")
    assert record.template_id == "generic-flat"


def test_start_leaves_template_id_none_when_not_given(service):
    record = service.start("job-1", "DWG-1", "A", [_balloon(1)])
    assert record.template_id is None


def test_get_status_before_any_review(service):
    service.start("job-1", "DWG-1", "A", [_balloon(1), _balloon(2)])

    status = service.get_status("job-1")

    assert status.total_balloons == 2
    assert status.pending == 2
    assert status.reconciled == 0
    assert status.percent_complete == 0.0
    assert status.ready_for_signoff is False


def test_get_status_raises_job_not_found_for_unknown_job(service):
    with pytest.raises(JobNotFoundError):
        service.get_status("does-not-exist")


class TestReviewBalloon:
    def test_confirm_marks_reconciled_with_no_discrepancy(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)])

        result = service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CONFIRM)

        assert result.status == BalloonReviewStatus.RECONCILED
        assert result.discrepancy is False
        assert result.reviewed.nominal_value == 25.4
        assert result.reviewer_id == "bob"
        assert result.reviewed_at is not None

    def test_correct_with_a_different_value_sets_discrepancy(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1, nominal=25.4)])
        corrected = _balloon(1, nominal=25.5)

        result = service.review_balloon(
            "job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CORRECT, corrected=corrected
        )

        assert result.status == BalloonReviewStatus.RECONCILED
        assert result.discrepancy is True
        assert result.reviewed.nominal_value == 25.5

    def test_correct_with_an_identical_value_does_not_set_discrepancy(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1, nominal=25.4)])
        same_value = _balloon(1, nominal=25.4)

        result = service.review_balloon(
            "job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CORRECT, corrected=same_value
        )

        assert result.discrepancy is False

    def test_correct_without_a_corrected_value_raises_validation_error(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)])

        with pytest.raises(ValidationError, match="correctedValue"):
            service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CORRECT)

    def test_cannot_determine_requires_notes(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)])

        with pytest.raises(ValidationError, match="notes"):
            service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CANNOT_DETERMINE)

    def test_cannot_determine_with_notes_sets_status_and_clears_reviewed_value(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)])

        result = service.review_balloon(
            "job-1", page=1, balloon_number=1, reviewer_id="bob",
            action=ReviewAction.CANNOT_DETERMINE, notes="Smudged ink, illegible on the source scan.",
        )

        assert result.status == BalloonReviewStatus.CANNOT_DETERMINE
        assert result.reviewed is None
        assert result.notes == "Smudged ink, illegible on the source scan."

    def test_rejects_missing_reviewer_id(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)])

        with pytest.raises(ValidationError, match="reviewerId"):
            service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="", action=ReviewAction.CONFIRM)

    def test_rejects_unknown_balloon(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)])

        with pytest.raises(BalloonNotFoundError):
            service.review_balloon("job-1", page=1, balloon_number=99, reviewer_id="bob", action=ReviewAction.CONFIRM)

    def test_segregation_of_duties_blocks_the_submitter_from_reviewing(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)], submitted_by="alice")

        with pytest.raises(SegregationOfDutiesError):
            service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="alice", action=ReviewAction.CONFIRM)

    def test_no_segregation_check_when_submitted_by_was_never_recorded(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)], submitted_by=None)

        # doesn't raise -- there's nothing to segregate from if identity was never captured
        result = service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="anyone", action=ReviewAction.CONFIRM)
        assert result.status == BalloonReviewStatus.RECONCILED

    def test_rejects_further_review_after_signoff(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)])
        service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CONFIRM)
        service.sign_off("job-1", "bob")

        with pytest.raises(ValidationError, match="already been signed off"):
            service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="carol", action=ReviewAction.CONFIRM)

    def test_balloon_number_alone_is_not_a_unique_key_across_pages(self, service):
        """Balloon numbering can restart per page (see the multi-page integration test) --
        review must key on (page, balloon_number), not balloon_number alone."""
        service.start("job-1", "DWG-1", "A", [_balloon(1, page=1, nominal=5.0), _balloon(1, page=2, nominal=7.0)])

        service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CONFIRM)
        status = service.get_status("job-1")

        assert status.reconciled == 1
        assert status.pending == 1  # page 2's balloon 1 is untouched


class TestSignOff:
    def test_blocks_signoff_while_any_balloon_is_pending(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1), _balloon(2)])
        service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CONFIRM)

        with pytest.raises(IncompleteReconciliationError) as excinfo:
            service.sign_off("job-1", "carol")
        assert excinfo.value.open_balloons == [(1, 2)]

    def test_blocks_signoff_while_a_balloon_is_cannot_determine(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)])
        service.review_balloon(
            "job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CANNOT_DETERMINE, notes="illegible"
        )

        with pytest.raises(IncompleteReconciliationError):
            service.sign_off("job-1", "carol")

    def test_segregation_of_duties_blocks_the_submitter_from_signing(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)], submitted_by="alice")
        service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CONFIRM)

        with pytest.raises(SegregationOfDutiesError):
            service.sign_off("job-1", "alice")

    def test_succeeds_once_every_balloon_is_reconciled(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1), _balloon(2)], submitted_by="alice")
        service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CONFIRM)
        service.review_balloon("job-1", page=1, balloon_number=2, reviewer_id="bob", action=ReviewAction.CONFIRM)

        record = service.sign_off("job-1", "bob")

        assert record.signed_off is True
        assert record.signed_off_by == "bob"
        assert record.signed_off_at is not None
        assert service.get_status("job-1").signed_off is True

    def test_rejects_missing_signer_id(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)])
        service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CONFIRM)

        with pytest.raises(ValidationError, match="signerId"):
            service.sign_off("job-1", "")


class TestGetReconciledBalloons:
    def test_blocked_before_signoff(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1)])
        service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CONFIRM)

        with pytest.raises(IncompleteReconciliationError):
            service.get_reconciled_balloons("job-1")

    def test_uses_the_corrected_value_when_the_reviewer_changed_it(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1, nominal=25.4)], submitted_by="alice")
        service.review_balloon(
            "job-1", page=1, balloon_number=1, reviewer_id="bob",
            action=ReviewAction.CORRECT, corrected=_balloon(1, nominal=25.5),
        )
        service.sign_off("job-1", "bob")

        [final] = service.get_reconciled_balloons("job-1")

        assert final.nominal_value == 25.5
        assert final.confidence == 1.0  # human-verified, not the raw AI confidence
        assert "reviewer-corrected" in final.notes

    def test_uses_the_extracted_value_when_the_reviewer_confirmed_it_unchanged(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1, nominal=25.4)])
        service.review_balloon("job-1", page=1, balloon_number=1, reviewer_id="bob", action=ReviewAction.CONFIRM)
        service.sign_off("job-1", "bob")

        [final] = service.get_reconciled_balloons("job-1")

        assert final.nominal_value == 25.4
        assert "reviewer-confirmed" in final.notes

    def test_results_are_sorted_by_page_then_balloon_number(self, service):
        service.start(
            "job-1", "DWG-1", "A",
            [_balloon(2, page=1), _balloon(1, page=1), _balloon(1, page=2)],
        )
        for page, number in [(1, 1), (1, 2), (2, 1)]:
            service.review_balloon("job-1", page=page, balloon_number=number, reviewer_id="bob", action=ReviewAction.CONFIRM)
        service.sign_off("job-1", "bob")

        finalized = service.get_reconciled_balloons("job-1")

        assert [(b.page, b.balloon_number) for b in finalized] == [(1, 1), (1, 2), (2, 1)]


class TestGetExtractedBalloons:
    def test_returns_the_raw_ai_values_unaffected_by_a_correction(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1, nominal=25.4)])
        service.review_balloon(
            "job-1", page=1, balloon_number=1, reviewer_id="bob",
            action=ReviewAction.CORRECT, corrected=_balloon(1, nominal=25.5),
        )

        [extracted] = service.get_extracted_balloons("job-1")

        assert extracted.nominal_value == 25.4  # the model's original output, not the reviewer's correction
        assert extracted.confidence == 0.9  # not bumped to 1.0 the way get_reconciled_balloons's output is

    def test_available_before_signoff_and_before_any_review(self, service):
        service.start("job-1", "DWG-1", "A", [_balloon(1), _balloon(2)])

        # unlike get_reconciled_balloons, this doesn't raise IncompleteReconciliationError
        extracted = service.get_extracted_balloons("job-1")

        assert len(extracted) == 2

    def test_results_are_sorted_by_page_then_balloon_number(self, service):
        service.start(
            "job-1", "DWG-1", "A",
            [_balloon(2, page=1), _balloon(1, page=1), _balloon(1, page=2)],
        )

        extracted = service.get_extracted_balloons("job-1")

        assert [(b.page, b.balloon_number) for b in extracted] == [(1, 1), (1, 2), (2, 1)]

    def test_raises_job_not_found_for_unknown_job(self, service):
        with pytest.raises(JobNotFoundError):
            service.get_extracted_balloons("does-not-exist")


class TestParseReviewRequest:
    def test_rejects_missing_reviewer_id(self):
        with pytest.raises(ValidationError, match="reviewerId"):
            ReconciliationService.parse_review_request({"action": "confirm"})

    def test_rejects_invalid_action(self):
        with pytest.raises(ValidationError, match="Invalid 'action'"):
            ReconciliationService.parse_review_request({"reviewerId": "bob", "action": "approve"})

    def test_rejects_malformed_corrected_value(self):
        with pytest.raises(ValidationError, match="correctedValue"):
            ReconciliationService.parse_review_request(
                {"reviewerId": "bob", "action": "correct", "correctedValue": {"balloon_number": "not-a-number"}}
            )

    def test_parses_a_well_formed_confirm_request(self):
        reviewer_id, action, corrected, notes = ReconciliationService.parse_review_request(
            {"reviewerId": "bob", "action": "confirm"}
        )
        assert reviewer_id == "bob"
        assert action == ReviewAction.CONFIRM
        assert corrected is None
        assert notes is None
