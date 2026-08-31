"""Registry of named Excel export templates -- FR-17 ("configurable per customer/program", a new
template is a config change, not a code change where possible) and the "export into the specified
structured template" ask this registry exists to satisfy.

A template that only relabels/reorders columns is pure config (see GENERIC_FLAT). A template that
needs composed/derived text -- AS9102 Form 3's "Requirement" column is nominal+tolerance *or* a
GD&T frame, whichever the balloon actually has, not a single raw field -- registers its own
`row_values` function alongside its columns; that's still one place to look, not scattered
`if template_id == ...` branches in ExcelWriter itself.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.exceptions import UnknownTemplateError
from src.models import ExtractedBalloon

RowValuesFn = Callable[[ExtractedBalloon, "str | None", "str | None"], dict]


@dataclass(frozen=True)
class ExcelTemplate:
    template_id: str
    name: str
    description: str
    sheet_title: str
    columns: list[tuple[str, str]]  # (field_key, header), left to right
    row_values: RowValuesFn  # (balloon, drawing_number, revision) -> {field_key: value}
    # Whether to render a drawing/revision/generated-at title block above the table -- AS9102
    # Form 3 has one, a flat characteristics list doesn't need one.
    title_block: bool = False


def _generic_row_values(balloon: ExtractedBalloon, drawing_number: str | None, revision: str | None) -> dict:
    notes = balloon.notes or ""
    if balloon.extraction_error:
        notes = f"{notes} [ERROR: {balloon.extraction_error}]".strip()
    return {
        "balloon_number": balloon.balloon_number,
        "drawing_number": drawing_number,
        "revision": revision,
        "page": balloon.page,
        "nominal_value": balloon.nominal_value,
        "unit": balloon.unit,
        "tolerance_type": balloon.tolerance_type.value if balloon.tolerance_type else None,
        "upper_tol": balloon.upper_tol,
        "lower_tol": balloon.lower_tol,
        "gdt_symbol": balloon.gdt.symbol if balloon.gdt else None,
        "gdt_value": balloon.gdt.value if balloon.gdt else None,
        "gdt_datums": ", ".join(balloon.gdt.datums) if balloon.gdt else None,
        "confidence": round(balloon.confidence, 2),
        "notes": notes,
    }


GENERIC_FLAT = ExcelTemplate(
    template_id="generic-flat",
    name="Generic flat characteristics list",
    description="One row per balloon, every extracted field as its own column. No title block.",
    sheet_title="Characteristics",
    columns=[
        ("balloon_number", "Balloon #"),
        ("drawing_number", "Drawing No."),
        ("revision", "Rev"),
        ("page", "Sheet"),
        ("nominal_value", "Nominal"),
        ("unit", "Unit"),
        ("tolerance_type", "Tol. Type"),
        ("upper_tol", "Upper Tol"),
        ("lower_tol", "Lower Tol"),
        ("gdt_symbol", "GD&T Symbol"),
        ("gdt_value", "GD&T Value"),
        ("gdt_datums", "Datums"),
        ("confidence", "Confidence"),
        ("notes", "Notes"),
    ],
    row_values=_generic_row_values,
)


def _describe_requirement(balloon: ExtractedBalloon) -> str:
    """Human-readable drawing requirement for AS9102 Form 3's Requirement column -- nominal value
    + tolerance and/or the GD&T frame, whichever the balloon actually carries."""
    parts: list[str] = []
    if balloon.nominal_value is not None:
        value = f"{balloon.nominal_value}"
        if balloon.unit:
            value += f" {balloon.unit}"
        if balloon.upper_tol is not None or balloon.lower_tol is not None:
            upper = balloon.upper_tol if balloon.upper_tol is not None else 0
            lower = balloon.lower_tol if balloon.lower_tol is not None else 0
            value += f" (+{upper}/{lower})"
        elif balloon.tolerance_type:
            value += f" [{balloon.tolerance_type.value}]"
        parts.append(value)
    if balloon.gdt:
        modifiers = f" ({', '.join(balloon.gdt.modifiers)})" if balloon.gdt.modifiers else ""
        datums = f" | datums {'-'.join(balloon.gdt.datums)}" if balloon.gdt.datums else ""
        parts.append(f"{balloon.gdt.symbol} {balloon.gdt.value}{modifiers}{datums}")
    return "; ".join(parts) or "—"


def _as9102_row_values(balloon: ExtractedBalloon, drawing_number: str | None, revision: str | None) -> dict:
    notes = balloon.notes or ""
    if balloon.extraction_error:
        notes = f"{notes} [ERROR: {balloon.extraction_error}]".strip()
    return {
        "balloon_number": balloon.balloon_number,
        "page": f"Sheet {balloon.page}",
        "requirement": _describe_requirement(balloon),
        "confidence": round(balloon.confidence, 2),
        "notes": notes,
    }


AS9102_FORM3 = ExcelTemplate(
    template_id="as9102-form3",
    name="AS9102 Form 3 (characteristic accountability)",
    description=(
        "AS9102 Form 3-inspired layout: a drawing/revision title block, then one row per "
        "characteristic with its drawing requirement. Not a byte-exact reproduction of the "
        "official form -- Results/inspection-disposition columns are intentionally omitted, "
        "since this system extracts drawing requirements, it doesn't perform inspection."
    ),
    sheet_title="Form 3",
    columns=[
        ("balloon_number", "Char. No."),
        ("page", "Reference Location"),
        ("requirement", "Requirement (Nominal / Tolerance / GD&T)"),
        ("confidence", "Extraction Confidence"),
        ("notes", "Notes"),
    ],
    row_values=_as9102_row_values,
    title_block=True,
)

TEMPLATES: dict[str, ExcelTemplate] = {t.template_id: t for t in (AS9102_FORM3, GENERIC_FLAT)}
DEFAULT_TEMPLATE_ID = AS9102_FORM3.template_id


def get_template(template_id: str | None) -> ExcelTemplate:
    """Resolves a templateId to its ExcelTemplate, falling back to the default when None/empty.
    Raises UnknownTemplateError (-> HTTP 400) for anything else not registered above."""
    key = template_id or DEFAULT_TEMPLATE_ID
    try:
        return TEMPLATES[key]
    except KeyError as exc:
        valid = ", ".join(sorted(TEMPLATES))
        raise UnknownTemplateError(f"Unknown export template '{key}'. Valid values: {valid}.") from exc


def list_templates() -> list[ExcelTemplate]:
    return sorted(TEMPLATES.values(), key=lambda t: t.template_id)
