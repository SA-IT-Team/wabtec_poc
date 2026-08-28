"""Deterministic (non-AI) data-quality checks over a reconciliation record -- the concrete, always
-reproducible half of the chatbot's analysis pass (see chat_assistant.py). Kept separate from the
AI call on purpose: every finding here is independently verifiable by re-reading this file, with no
API call and no model non-determinism, and chat_assistant.py hands these to Claude as grounding
rather than asking it to re-derive arithmetic/counting it can get wrong.

Covers requirements.md's four callouts: missing information, incomplete data, inconsistencies
between sheets, and common mistakes.
"""
from __future__ import annotations

from collections import defaultdict

from src.models import (
    AnalysisFinding,
    AnalysisFindingCategory,
    AnalysisFindingSeverity,
    BalloonRef,
    BalloonReviewRecord,
    BalloonReviewStatus,
    ExtractedBalloon,
    ReconciliationRecord,
    ToleranceType,
)

LOW_CONFIDENCE_THRESHOLD = 0.7

# GD&T characteristics that are effectively meaningless without at least one datum reference --
# ASME Y14.5 / ISO 1101 orientation, location, and runout controls all require one.
_DATUM_REQUIRING_SYMBOLS = {
    "position",
    "profile of a surface",
    "profile of a line",
    "perpendicularity",
    "parallelism",
    "angularity",
    "concentricity",
    "symmetry",
    "runout",
    "total runout",
}

_TOLERANCE_TYPES_REQUIRING_VALUES = {ToleranceType.BILATERAL, ToleranceType.UNILATERAL, ToleranceType.LIMIT}


def run_checks(record: ReconciliationRecord) -> list[AnalysisFinding]:
    """Runs every rule against the record's current best-known values (a reviewer's correction
    wins over the raw extraction, same precedence as export) and returns every finding, unsorted."""
    findings: list[AnalysisFinding] = []
    findings.extend(_missing_information(record.balloons))
    findings.extend(_incomplete_data(record.balloons))
    findings.extend(_sheet_inconsistencies(record.balloons))
    findings.extend(_common_mistakes(record.balloons))
    return findings


def _current(b: BalloonReviewRecord) -> ExtractedBalloon:
    return b.reviewed if b.reviewed is not None else b.extracted


def _ref(b: BalloonReviewRecord) -> BalloonRef:
    return BalloonRef(page=b.page, balloon_number=b.balloon_number)


def _severity_or_downgrade(b: BalloonReviewRecord, base: AnalysisFindingSeverity) -> AnalysisFindingSeverity:
    """A balloon a human has already reviewed isn't something the reader still needs to act on --
    it stays in the report for audit visibility, just downgraded to informational."""
    if b.status == BalloonReviewStatus.PENDING:
        return base
    return AnalysisFindingSeverity.INFO


def _missing_information(balloons: list[BalloonReviewRecord]) -> list[AnalysisFinding]:
    out: list[AnalysisFinding] = []
    for b in balloons:
        v = _current(b)
        if v.extraction_error or v.blocked:
            continue  # already covered under incomplete data -- don't double-report the same balloon
        if v.nominal_value is None and v.gdt is None:
            out.append(
                AnalysisFinding(
                    category=AnalysisFindingCategory.MISSING_INFO,
                    severity=_severity_or_downgrade(b, AnalysisFindingSeverity.WARNING),
                    summary=f"Balloon {v.balloon_number} (p.{b.page}) has no nominal value or GD&T frame",
                    detail=(
                        "Neither a dimension nor a feature control frame was recorded for this balloon. "
                        "It may be a non-dimensional callout (note, finish, material) that's fine as-is, "
                        "or the extraction missed it -- worth a quick check against the source."
                    ),
                    balloon_refs=[_ref(b)],
                )
            )
        elif v.nominal_value is not None and not v.unit:
            out.append(
                AnalysisFinding(
                    category=AnalysisFindingCategory.MISSING_INFO,
                    severity=_severity_or_downgrade(b, AnalysisFindingSeverity.WARNING),
                    summary=f"Balloon {v.balloon_number} (p.{b.page}) has a value but no unit",
                    detail="A bare number without mm/in is ambiguous downstream (Excel export, CMM programming).",
                    balloon_refs=[_ref(b)],
                )
            )
    return out


def _incomplete_data(balloons: list[BalloonReviewRecord]) -> list[AnalysisFinding]:
    """Checked against the *original* AI extraction (not a reviewer's correction) -- this is about
    extraction quality, which doesn't change just because a human later fixed the value."""
    out: list[AnalysisFinding] = []
    for b in balloons:
        e = b.extracted
        if e.extraction_error:
            out.append(
                AnalysisFinding(
                    category=AnalysisFindingCategory.INCOMPLETE_DATA,
                    severity=_severity_or_downgrade(b, AnalysisFindingSeverity.CRITICAL),
                    summary=f"Balloon {e.balloon_number} (p.{b.page}) failed extraction",
                    detail=f"Extraction error: {e.extraction_error}",
                    balloon_refs=[_ref(b)],
                )
            )
        elif e.blocked:
            out.append(
                AnalysisFinding(
                    category=AnalysisFindingCategory.INCOMPLETE_DATA,
                    severity=_severity_or_downgrade(b, AnalysisFindingSeverity.CRITICAL),
                    summary=f"Balloon {e.balloon_number} (p.{b.page}) was blocked during extraction",
                    detail="The model's content filter tripped on this balloon's region; it needs manual entry.",
                    balloon_refs=[_ref(b)],
                )
            )
        elif e.confidence < LOW_CONFIDENCE_THRESHOLD:
            out.append(
                AnalysisFinding(
                    category=AnalysisFindingCategory.INCOMPLETE_DATA,
                    severity=_severity_or_downgrade(b, AnalysisFindingSeverity.WARNING),
                    summary=f"Balloon {e.balloon_number} (p.{b.page}) was extracted with low confidence ({e.confidence:.0%})",
                    detail=f"Below the {LOW_CONFIDENCE_THRESHOLD:.0%} threshold this build treats as reliable -- worth a closer look at the source.",
                    balloon_refs=[_ref(b)],
                )
            )
    return out


def _sheet_inconsistencies(balloons: list[BalloonReviewRecord]) -> list[AnalysisFinding]:
    out: list[AnalysisFinding] = []

    by_number: dict[int, list[BalloonReviewRecord]] = defaultdict(list)
    for b in balloons:
        by_number[b.balloon_number].append(b)

    for number, group in sorted(by_number.items()):
        pages = {b.page for b in group}
        if len(group) > 1 and len(pages) == 1:
            # Same balloon number repeated on the same page -- a data-integrity bug, not a
            # cross-sheet question (FR-08 is supposed to prevent this upstream of reconciliation).
            out.append(
                AnalysisFinding(
                    category=AnalysisFindingCategory.INCONSISTENCY,
                    severity=AnalysisFindingSeverity.CRITICAL,
                    summary=f"Balloon {number} appears more than once on page {group[0].page}",
                    detail="Duplicate balloon numbers on one sheet should have been flagged before reconciliation; resolve which entry is correct.",
                    balloon_refs=[_ref(b) for b in group],
                )
            )
            continue

        if len(pages) < 2:
            continue  # single sheet, nothing to cross-check

        signatures = {
            (
                round(v.nominal_value, 6) if v.nominal_value is not None else None,
                v.unit,
                v.tolerance_type,
                round(v.upper_tol, 6) if v.upper_tol is not None else None,
                round(v.lower_tol, 6) if v.lower_tol is not None else None,
            )
            for v in (_current(b) for b in group)
        }
        if len(signatures) > 1:
            out.append(
                AnalysisFinding(
                    category=AnalysisFindingCategory.INCONSISTENCY,
                    severity=AnalysisFindingSeverity.WARNING,
                    summary=f"Balloon {number} disagrees across sheets {sorted(pages)}",
                    detail=(
                        "The same balloon number carries different value/unit/tolerance data on "
                        "different sheets. Could be an intentional per-sheet callout, or a "
                        "mis-transcription -- worth confirming against the drawing."
                    ),
                    balloon_refs=[_ref(b) for b in group],
                )
            )
    return out


def _common_mistakes(balloons: list[BalloonReviewRecord]) -> list[AnalysisFinding]:
    out: list[AnalysisFinding] = []
    units_seen: dict[str, list[BalloonReviewRecord]] = defaultdict(list)

    for b in balloons:
        v = _current(b)

        if v.gdt and v.gdt.symbol.strip().lower() in _DATUM_REQUIRING_SYMBOLS and not v.gdt.datums:
            out.append(
                AnalysisFinding(
                    category=AnalysisFindingCategory.COMMON_MISTAKE,
                    severity=_severity_or_downgrade(b, AnalysisFindingSeverity.WARNING),
                    summary=f"Balloon {v.balloon_number} (p.{b.page}): {v.gdt.symbol} frame has no datum reference",
                    detail=f"A '{v.gdt.symbol}' control is normally meaningless without at least one datum -- check whether one was missed on the drawing or in extraction.",
                    balloon_refs=[_ref(b)],
                )
            )

        if (
            v.tolerance_type in _TOLERANCE_TYPES_REQUIRING_VALUES
            and v.upper_tol is None
            and v.lower_tol is None
            and v.nominal_value is not None
        ):
            out.append(
                AnalysisFinding(
                    category=AnalysisFindingCategory.COMMON_MISTAKE,
                    severity=_severity_or_downgrade(b, AnalysisFindingSeverity.WARNING),
                    summary=f"Balloon {v.balloon_number} (p.{b.page}) has a {v.tolerance_type.value} tolerance type but no tolerance values",
                    detail="Tolerance type was recorded but upper/lower tolerance are both empty -- likely an incomplete extraction of the tolerance block.",
                    balloon_refs=[_ref(b)],
                )
            )

        if v.unit:
            units_seen[v.unit].append(b)

    if len(units_seen) > 1:
        dominant_unit = max(units_seen, key=lambda u: len(units_seen[u]))
        minority = [b for unit, group in units_seen.items() if unit != dominant_unit for b in group]
        out.append(
            AnalysisFinding(
                category=AnalysisFindingCategory.COMMON_MISTAKE,
                severity=AnalysisFindingSeverity.WARNING,
                summary=f"Mixed units on one drawing ({', '.join(sorted(units_seen))})",
                detail=(
                    f"Most balloons use '{dominant_unit}'; {len(minority)} use a different unit. "
                    "Could be a legitimately dual-dimensioned drawing, or a unit mis-extraction -- worth confirming."
                ),
                balloon_refs=[_ref(b) for b in minority],
            )
        )

    return out
