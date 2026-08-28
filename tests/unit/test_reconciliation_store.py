"""InMemoryReconciliationStore behavior, plus a JSON round-trip check on ReconciliationRecord
itself (the shape BlobReconciliationStore persists verbatim via model_dump_json/model_validate --
this catches datetime/enum serialization issues without needing real Blob Storage; the real
network-calling class isn't unit tested here, consistent with this project's existing precedent
for TableStorageJobStore -- see README.md).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.exceptions import JobNotFoundError
from src.models import BalloonReviewRecord, ExtractedBalloon, ReconciliationRecord
from src.reconciliation_store import InMemoryReconciliationStore


def test_load_raises_job_not_found_for_unknown_job():
    store = InMemoryReconciliationStore()
    with pytest.raises(JobNotFoundError):
        store.load("does-not-exist")


def test_save_then_load_round_trips():
    store = InMemoryReconciliationStore()
    record = ReconciliationRecord(
        job_id="job-1",
        drawing_number="DWG-1",
        revision="A",
        balloons=[BalloonReviewRecord(page=1, balloon_number=1, extracted=ExtractedBalloon(balloon_number=1, page=1))],
        created_at=datetime.now(timezone.utc),
    )

    store.save(record)

    assert store.exists("job-1") is True
    assert store.load("job-1") == record


def test_exists_is_false_for_unknown_job():
    store = InMemoryReconciliationStore()
    assert store.exists("does-not-exist") is False


def test_reconciliation_record_round_trips_through_json():
    """Exercises exactly what BlobReconciliationStore does: model_dump_json() -> json.loads() ->
    model_validate() -- the datetime/enum fields are the ones most likely to break silently."""
    original = ReconciliationRecord(
        job_id="job-1",
        drawing_number="DWG-1",
        revision="C",
        submitted_by="alice",
        balloons=[
            BalloonReviewRecord(
                page=1,
                balloon_number=12,
                extracted=ExtractedBalloon(balloon_number=12, page=1, nominal_value=25.4, confidence=0.9),
                reviewed=ExtractedBalloon(balloon_number=12, page=1, nominal_value=25.5, confidence=1.0),
                discrepancy=True,
                reviewer_id="bob",
                reviewed_at=datetime.now(timezone.utc),
                notes="corrected per source drawing",
            )
        ],
        signed_off=True,
        signed_off_by="bob",
        signed_off_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )

    round_tripped = ReconciliationRecord.model_validate(json.loads(original.model_dump_json()))

    assert round_tripped == original
