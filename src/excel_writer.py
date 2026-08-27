"""ExcelWriter: maps normalized balloon data onto a configurable column layout (FR-17). The column
list is a plain (field_key, header) list rather than hard-coded cell writes, so a new template is a
config change, not a code change -- see architecture-poc.md §4.2 / requirements.md NFR "Maintainability".
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from src.models import ExtractedBalloon

DEFAULT_TEMPLATE_COLUMNS: list[tuple[str, str]] = [
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
]

LOW_CONFIDENCE_THRESHOLD = 0.7
HEADER_FILL = PatternFill(start_color="1C2431", end_color="1C2431", fill_type="solid")
LOW_CONFIDENCE_FILL = PatternFill(start_color="F3E7C9", end_color="F3E7C9", fill_type="solid")


class ExcelWriter:
    def __init__(
        self,
        columns: list[tuple[str, str]] | None = None,
        template_id: str = "as9102-form3",
    ):
        self._columns = columns or DEFAULT_TEMPLATE_COLUMNS
        self._template_id = template_id

    def write(
        self,
        *,
        drawing_number: str | None,
        revision: str | None,
        balloons: list[ExtractedBalloon],
    ) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "Characteristics"

        for col_idx, (_, header) in enumerate(self._columns, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = HEADER_FILL

        for row_idx, balloon in enumerate(sorted(balloons, key=lambda b: b.balloon_number), start=2):
            row_values = self._row_values(balloon, drawing_number, revision)
            for col_idx, (key, _) in enumerate(self._columns, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=row_values.get(key))
                if balloon.confidence < LOW_CONFIDENCE_THRESHOLD:
                    cell.fill = LOW_CONFIDENCE_FILL

        for col_idx in range(1, len(self._columns) + 1):
            ws.column_dimensions[self._column_letter(col_idx)].width = 16

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    @staticmethod
    def _row_values(balloon: ExtractedBalloon, drawing_number: str | None, revision: str | None) -> dict:
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

    @staticmethod
    def _column_letter(col_idx: int) -> str:
        letters = ""
        while col_idx > 0:
            col_idx, remainder = divmod(col_idx - 1, 26)
            letters = chr(65 + remainder) + letters
        return letters
