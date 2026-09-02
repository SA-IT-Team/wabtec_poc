"""TitleBlockExtractor: best-effort regex heuristic over Document Intelligence's OCR line text,
pulling drawing number and revision out of a title block (FR-02: "On upload, the system shall
extract title-block metadata (drawing number, revision, sheet count, title) via OCR"). This was
documented from the start but never actually wired up -- PipelineContext.drawing_number/.revision
existed as fields nothing ever set, so every extraction, review screen, and export showed a blank
Drawing/Rev regardless of what was actually on the sheet.

Like BalloonDetector, this is a POC heuristic, not a trained field-extraction model: Document
Intelligence's prebuilt-layout returns lines of OCR'd text in roughly reading order, with no concept
of "this text is inside the DWG. NO. cell" -- there's no table/cell structure to lean on. This
recognizes a few common real-world title-block conventions:

  1. The standard ASME/ANSI sheet-format corner -- three adjacent header cells, SIZE / DWG. NO. /
     REV, immediately above their three value cells, e.g. B / CW-2045 / A. This is what SolidWorks'
     and most mechanical CAD tools' default title block templates produce. Document Intelligence
     may OCR the header row (and/or the value row) as one combined line ("SIZE DWG. NO. REV") or
     as one line per cell ("SIZE", then "DWG. NO.", then "REV") depending on how tightly the cells
     are spaced on the actual sheet -- this handles both without caring which one happened.
  2. A label and its value on the same line: "DWG NO: CW-2045", "REV. A", "PART NUMBER CW-2045".
  3. A label on its own line immediately followed by a value-only line (a simple two-cell layout
     without the SIZE/REV row alongside it).

An unusual title block layout producing no match is expected, not a bug to chase down here -- see
architecture-poc.md §5. This requires an explicit label before treating text as a candidate value,
deliberately, so it never guesses at arbitrary drawing text that happens to look plausible -- and
never lets one field's label be mistaken for another field's value (e.g. reading "REV" itself as
the drawing number, when DWG. NO. and REV land on adjacent OCR lines with no value between them).
"""
from __future__ import annotations

import re
from typing import Any, NamedTuple, Optional


class TitleBlockFields(NamedTuple):
    drawing_number: Optional[str]
    revision: Optional[str]


_DWG_LABEL = r"(?:DWG\.?\s*NO\.?|DRAWING\s*NO\.?|DRAWING\s*NUMBER|PART\s*NO\.?|PART\s*NUMBER|DOC(?:UMENT)?\.?\s*NO\.?)"
_REV_LABEL = r"(?:REV(?:ISION)?\.?)"
_SIZE_LABEL = r"SIZE"

_VALUE = r"[A-Z0-9][A-Z0-9\-_/.]{0,19}"
_REV_VALUE = r"[A-Z0-9]{1,3}"

_DWG_INLINE = re.compile(rf"{_DWG_LABEL}\s*[:#.\-]?\s*({_VALUE})", re.IGNORECASE)
_DWG_LABEL_ONLY = re.compile(rf"^{_DWG_LABEL}\s*[:.\-]?\s*$", re.IGNORECASE)
_DWG_VALUE_ONLY = re.compile(rf"^{_VALUE}$", re.IGNORECASE)

_REV_INLINE = re.compile(rf"{_REV_LABEL}\s*[:#.\-]?\s*({_REV_VALUE})\b", re.IGNORECASE)
_REV_LABEL_ONLY = re.compile(rf"^{_REV_LABEL}\s*[:.\-]?\s*$", re.IGNORECASE)
_REV_VALUE_ONLY = re.compile(rf"^{_REV_VALUE}$", re.IGNORECASE)

_SIZE_LABEL_ONLY = re.compile(rf"^{_SIZE_LABEL}\s*[:.\-]?\s*$", re.IGNORECASE)
_SIZE_DWG_REV_HEADER = re.compile(rf"^{_SIZE_LABEL}\s+{_DWG_LABEL}\s+{_REV_LABEL}$", re.IGNORECASE)

# Guards against a label bleeding into an adjacent field's slot -- e.g. DWG. NO. and REV landing on
# consecutive OCR lines with no value between them (a multi-cell header row OCR'd as separate
# lines) would otherwise read "REV" itself as if it were the drawing number.
_LABEL_WORDS = {"REV", "REVISION", "SIZE", "DWG", "NO", "PART", "NUMBER", "DRAWING", "DOC", "DOCUMENT"}


class TitleBlockExtractor:
    def extract(self, layout: dict[str, Any]) -> TitleBlockFields:
        lines = [(line.get("content") or "").strip() for line in layout.get("lines", [])]
        lines = [line for line in lines if line]

        drawing_number = self._from_size_dwg_rev_block(lines, column=1) or self._find(
            lines, _DWG_INLINE, _DWG_LABEL_ONLY, _DWG_VALUE_ONLY
        )
        revision = self._from_size_dwg_rev_block(lines, column=2) or self._find(
            lines, _REV_INLINE, _REV_LABEL_ONLY, _REV_VALUE_ONLY
        )
        return TitleBlockFields(drawing_number=drawing_number, revision=revision)

    @classmethod
    def _from_size_dwg_rev_block(cls, lines: list[str], column: int) -> Optional[str]:
        """column 1 = the DWG. NO. cell, column 2 = the REV cell (0 = SIZE, not used). Handles the
        header row as either one combined line or three separate label lines, and likewise for the
        value row right after it -- see the module docstring."""
        for i, line in enumerate(lines):
            if _SIZE_DWG_REV_HEADER.match(line):
                values = cls._next_tokens(lines, i + 1, 3)
            elif (
                _SIZE_LABEL_ONLY.match(line)
                and i + 2 < len(lines)
                and _DWG_LABEL_ONLY.match(lines[i + 1])
                and _REV_LABEL_ONLY.match(lines[i + 2])
            ):
                values = cls._next_tokens(lines, i + 3, 3)
            else:
                continue
            if values:
                return values[column]
        return None

    @staticmethod
    def _next_tokens(lines: list[str], start: int, count: int) -> Optional[list[str]]:
        """Flattens whitespace-split tokens from consecutive lines starting at `start` until
        `count` tokens are collected -- so a value row emitted as one combined line ("B CW-2045 A")
        or as one line per cell ("B" / "CW-2045" / "A") are read the same way. Stops at the first
        line containing more text than a single bare token once at least one token is already
        collected, so it doesn't wander into unrelated sheet notes below the title block."""
        tokens: list[str] = []
        i = start
        while i < len(lines) and len(tokens) < count:
            line_tokens = lines[i].split()
            if tokens and len(line_tokens) > 1:
                break
            tokens.extend(line_tokens)
            i += 1
        return tokens[:count] if len(tokens) >= count else None

    @staticmethod
    def _find(
        lines: list[str], inline: "re.Pattern[str]", label_only: "re.Pattern[str]", value_only: "re.Pattern[str]"
    ) -> Optional[str]:
        for i, line in enumerate(lines):
            match = inline.search(line)
            if match:
                value = match.group(1).strip().rstrip(".:")
                if value.upper() not in _LABEL_WORDS:
                    return value
                # otherwise this "value" was actually the next field's label bleeding across a
                # multi-label line (e.g. "SIZE DWG. NO. REV") -- keep scanning, not a real match.
            elif (
                label_only.match(line)
                # Only trust the simple "label line, then its value on the next line" shape when
                # this label is isolated -- not itself sitting right after another recognized
                # label. A label immediately preceded by another label means these are part of a
                # multi-cell header run (e.g. SIZE / DWG. NO. / REV) that _from_size_dwg_rev_block
                # already tried and, if it didn't return a value above, correctly declined to
                # guess at -- this fallback shouldn't second-guess that by pairing REV with
                # whatever token happens to follow it.
                and (i == 0 or not _is_label_only_line(lines[i - 1]))
                and i + 1 < len(lines)
                and lines[i + 1].strip().upper() not in _LABEL_WORDS
                and value_only.match(lines[i + 1])
            ):
                return lines[i + 1].strip()
        return None


def _is_label_only_line(line: str) -> bool:
    return bool(_SIZE_LABEL_ONLY.match(line) or _DWG_LABEL_ONLY.match(line) or _REV_LABEL_ONLY.match(line))
