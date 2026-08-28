"""ReconciliationService: the human quality-check pass requirements.md's first item names
directly -- "Perform a quality check/reconciliation to ensure 100% data accuracy against the
source drawings." Automated extraction alone can never satisfy that guarantee (there's no ground
truth to compare against -- that's what a human comparing the extracted value to the drawing IS);
this service is the workflow around a person doing exactly that, one balloon at a time.

State machine per balloon (BalloonReviewStatus): pending -> reconciled | cannot_determine.
A drawing is exportable only once every balloon is `reconciled` and the drawing is signed off --
see get_reconciled_balloons and IncompleteReconciliationError. Once signed off, the record is
locked: review_balloon refuses further changes (ValidationError) so the exported spreadsheet can
never silently drift from what was actually signed off.

Identity note: `reviewer_id` / `signer_id` / `submitted_by` are self-declared strings, not
authenticated identities -- this POC has no user auth at all (architecture-poc.md §3.3; app.py's
API_ACCESS_KEY gates whether a caller can talk to the backend at all, a different and weaker
guarantee than per-user identity). The segregation-of-duties check below (a reviewer can't be the
same person who submitted the drawing) is therefore a workflow rule enforced on whatever name was
typed in, not a security control. It's still worth enforcing: it catches the "I extracted it, and
then I '"reviewed"' it myself" shortcut, even though it can't stop someone from just typing a
different name.

Simplification vs. architecture-full.md's design: the full spec models a two-human disagreement
(analyst confirms, independent reviewer confirms, mismatches between the two need a third
resolution step -- FR-23). This POC has only one human review pass over the AI's output, so
"discrepancy" here means AI-extracted vs. human-reviewed, not human vs. human. There is
consequently no `unresolved` status distinct from `cannot_determine` -- a single reviewer's
correction is authoritative the moment they submit it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError as PydanticValidationError

from src.exceptions import (
    BalloonNotFoundError,
    IncompleteReconciliationError,
    SegregationOfDutiesError,
    ValidationError,
)
from src.models import (
    BalloonReviewRecord,
    BalloonReviewStatus,
    ExtractedBalloon,
    ReconciliationRecord,
    ReconciliationStatus,
    ReviewAction,
)
from src.reconciliation_store import IReconciliationStore


class ReconciliationService:
    def __init__(self, store: IReconciliationStore):
        self._store = store

    def start(
        self,
        job_id: str,
        drawing_number: Optional[str],
        revision: Optional[str],
        balloons: list[ExtractedBalloon],
        submitted_by: Optional[str] = None,
    ) -> ReconciliationRecord:
        """Seeds a fresh reconciliation record right after extraction -- every balloon starts
        `pending`. Overwrites any existing record for this job_id (extraction only ever runs once
        per job_id in this pipeline, so there's nothing to protect against re-running here)."""
        record = ReconciliationRecord(
            job_id=job_id,
            drawing_number=drawing_number,
            revision=revision,
            submitted_by=submitted_by or None,
            balloons=[
                BalloonReviewRecord(page=b.page, balloon_number=b.balloon_number, extracted=b)
                for b in balloons
            ],
            created_at=datetime.now(timezone.utc),
        )
        self._store.save(record)
        return record

    def review_balloon(
        self,
        job_id: str,
        page: int,
        balloon_number: int,
        reviewer_id: str,
        action: ReviewAction,
        corrected: Optional[ExtractedBalloon] = None,
        notes: Optional[str] = None,
    ) -> BalloonReviewRecord:
        if not reviewer_id or not reviewer_id.strip():
            raise ValidationError("reviewerId is required.")

        record = self._store.load(job_id)  # raises JobNotFoundError

        if record.signed_off:
            raise ValidationError(
                "This drawing has already been signed off; balloon reviews can no longer be changed. "
                "The exported record must stay identical to what was signed off."
            )

        if record.submitted_by and reviewer_id.strip() == record.submitted_by.strip():
            raise SegregationOfDutiesError(
                f"Reviewer '{reviewer_id}' must differ from the analyst who submitted this drawing "
                f"('{record.submitted_by}')."
            )

        target = next((b for b in record.balloons if b.page == page and b.balloon_number == balloon_number), None)
        if target is None:
            raise BalloonNotFoundError(f"No balloon {balloon_number} on page {page} for job '{job_id}'.")

        if action == ReviewAction.CONFIRM:
            target.reviewed = target.extracted.model_copy()
            target.discrepancy = False
            target.status = BalloonReviewStatus.RECONCILED
        elif action == ReviewAction.CORRECT:
            if corrected is None:
                raise ValidationError("action 'correct' requires a 'correctedValue' payload.")
            target.reviewed = corrected
            target.discrepancy = corrected.model_dump() != target.extracted.model_dump()
            target.status = BalloonReviewStatus.RECONCILED
        elif action == ReviewAction.CANNOT_DETERMINE:
            if not notes or not notes.strip():
                raise ValidationError("action 'cannot_determine' requires 'notes' explaining why (FR-25).")
            target.reviewed = None
            target.discrepancy = False
            target.status = BalloonReviewStatus.CANNOT_DETERMINE
        else:  # pragma: no cover - exhaustive over ReviewAction, defensive only
            raise ValidationError(f"Unknown review action: {action}")

        target.reviewer_id = reviewer_id.strip()
        target.reviewed_at = datetime.now(timezone.utc)
        target.notes = notes

        self._store.save(record)
        return target

    def get_record(self, job_id: str) -> ReconciliationRecord:
        """Full reconciliation state, every balloon -- what a review UI loads to render the
        side-by-side view (FR-21)."""
        return self._store.load(job_id)

    def get_status(self, job_id: str) -> ReconciliationStatus:
        record = self._store.load(job_id)
        return self._status_from_record(record)

    def sign_off(self, job_id: str, signer_id: str) -> ReconciliationRecord:
        if not signer_id or not signer_id.strip():
            raise ValidationError("signerId is required.")

        record = self._store.load(job_id)

        open_balloons = [
            (b.page, b.balloon_number) for b in record.balloons if b.status != BalloonReviewStatus.RECONCILED
        ]
        if open_balloons:
            raise IncompleteReconciliationError(
                f"{len(open_balloons)} of {len(record.balloons)} balloons are not yet reconciled.",
                open_balloons=open_balloons,
            )

        if record.submitted_by and signer_id.strip() == record.submitted_by.strip():
            raise SegregationOfDutiesError(
                f"Signer '{signer_id}' must differ from the analyst who submitted this drawing "
                f"('{record.submitted_by}')."
            )

        record.signed_off = True
        record.signed_off_by = signer_id.strip()
        record.signed_off_at = datetime.now(timezone.utc)
        self._store.save(record)
        return record

    def get_reconciled_balloons(self, job_id: str) -> list[ExtractedBalloon]:
        """The data an export is built from -- only callable once every balloon is reconciled and
        the drawing is signed off. Returns copies with confidence bumped to 1.0 (human-verified)
        and any leftover extraction_error cleared, so the exported spreadsheet reads as the
        verified record it now is, not raw AI output."""
        record = self._store.load(job_id)

        if not record.signed_off:
            open_balloons = [
                (b.page, b.balloon_number) for b in record.balloons if b.status != BalloonReviewStatus.RECONCILED
            ]
            raise IncompleteReconciliationError(
                "This drawing has not been signed off; export is blocked until it is.",
                open_balloons=open_balloons,
            )

        finalized: list[ExtractedBalloon] = []
        for b in record.balloons:
            source = b.reviewed if b.reviewed is not None else b.extracted
            note_suffix = " [reviewer-corrected]" if b.discrepancy else " [reviewer-confirmed]"
            finalized.append(
                source.model_copy(
                    update={
                        "confidence": 1.0,
                        "extraction_error": None,
                        "notes": f"{source.notes or ''}{note_suffix}".strip(),
                    }
                )
            )
        return sorted(finalized, key=lambda b: (b.page, b.balloon_number))

    @staticmethod
    def parse_review_request(
        payload: dict[str, Any],
    ) -> tuple[str, ReviewAction, Optional[ExtractedBalloon], Optional[str]]:
        """Parses+validates a raw review-endpoint JSON body into typed args for review_balloon.
        Never raises pydantic's own ValidationError, always this module's, so app.py only needs
        one except clause."""
        reviewer_id = payload.get("reviewerId")
        if not reviewer_id or not str(reviewer_id).strip():
            raise ValidationError("Request body must include 'reviewerId'.")

        raw_action = payload.get("action")
        try:
            action = ReviewAction(raw_action)
        except ValueError as exc:
            valid = ", ".join(a.value for a in ReviewAction)
            raise ValidationError(f"Invalid 'action' '{raw_action}'. Must be one of: {valid}.") from exc

        corrected = None
        if payload.get("correctedValue") is not None:
            try:
                corrected = ExtractedBalloon.model_validate(payload["correctedValue"])
            except PydanticValidationError as exc:
                raise ValidationError(f"Invalid 'correctedValue': {exc}") from exc

        notes = payload.get("notes")
        return str(reviewer_id), action, corrected, notes

    @staticmethod
    def _status_from_record(record: ReconciliationRecord) -> ReconciliationStatus:
        total = len(record.balloons)
        pending = sum(1 for b in record.balloons if b.status == BalloonReviewStatus.PENDING)
        reconciled = sum(1 for b in record.balloons if b.status == BalloonReviewStatus.RECONCILED)
        cannot_determine = sum(1 for b in record.balloons if b.status == BalloonReviewStatus.CANNOT_DETERMINE)
        percent_complete = round(100.0 * (reconciled + cannot_determine) / total, 1) if total else 0.0
        return ReconciliationStatus(
            job_id=record.job_id,
            total_balloons=total,
            pending=pending,
            reconciled=reconciled,
            cannot_determine=cannot_determine,
            percent_complete=percent_complete,
            ready_for_signoff=(total > 0 and reconciled == total),
            signed_off=record.signed_off,
            signed_off_by=record.signed_off_by,
            signed_off_at=record.signed_off_at,
        )
