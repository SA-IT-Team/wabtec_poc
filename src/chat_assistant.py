"""AI chatbot: general analysis + feedback assistant for one drawing's extraction/reconciliation
state (requirements.md's chatbot ask -- general analysis, missing information, incomplete data,
inconsistencies between sheets, common mistakes).

Two layers, deliberately kept separate:
  - src/analysis_rules.py: deterministic, non-AI checks -- always reproducible, no API call, no
    model non-determinism.
  - this module: hands those rule findings to Claude as grounding for a structured review pass
    (ChatAssistant.analyze), and answers free-form follow-up questions in the same grounded
    context (ChatAssistant.ask).

Grounding is built entirely from a ReconciliationRecord -- job_id, drawing_number, revision, and
every balloon's `extracted`/`reviewed` values -- so no separate data store is needed, and reviewer
corrections are represented as such (not silently merged): the assistant can distinguish "the model
saw X, a human corrected it to Y" from "the model saw X, unreviewed."

Both entry points are stateless here: `ask` takes the running conversation as an explicit `history`
argument rather than persisting one server-side (see wabtec_poc_app's ChatPanel, which keeps the
transcript client-side only) -- one more instance of this POC's "no server-side session state"
pattern (mirrors ReconciliationPanel's identity, kept in the browser, not a login).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.ai_clients import IChatCompletionClient
from src.analysis_rules import run_checks
from src.exceptions import ClaudeApiError
from src.models import (
    AnalysisFinding,
    AnalysisFindingSource,
    AnalysisReport,
    ReconciliationRecord,
)

logger = logging.getLogger(__name__)

ANALYSIS_TOOL_NAME = "record_drawing_analysis"
ANALYSIS_TOOL_DESCRIPTION = "Records the structured drawing analysis report (summary + findings)."

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "One or two sentence overview of this drawing's data quality and review state.",
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["missing_info", "incomplete_data", "inconsistency", "common_mistake"],
                    },
                    "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                    "summary": {"type": "string"},
                    "detail": {"type": "string"},
                    "balloon_refs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "page": {"type": "integer"},
                                "balloon_number": {"type": "integer"},
                            },
                            "required": ["page", "balloon_number"],
                        },
                    },
                },
                "required": ["category", "severity", "summary", "detail"],
            },
        },
    },
    "required": ["summary", "findings"],
}

CHAT_SYSTEM_PROMPT = (
    "You are a quality-engineering assistant helping review a ballooned engineering drawing's "
    "extracted dimension and tolerance data. You are given the drawing's extracted balloon data "
    "and its human reconciliation state (what a reviewer has confirmed or corrected). Answer "
    "questions about this specific drawing's data -- counts, values, tolerances, GD&T, review "
    "status -- grounded only in the data you were given. If asked about something not present in "
    "the data, say so rather than guessing. Be concise, and reference balloon numbers and page/"
    "sheet numbers when talking about specific callouts."
)

_ANALYSIS_SYSTEM_PROMPT = (
    CHAT_SYSTEM_PROMPT + " You are now producing a structured feedback report, not a conversational reply."
)


class ChatAssistant:
    def __init__(self, chat_client: IChatCompletionClient):
        self._chat_client = chat_client

    # ---------------------------------------------------------------- grounding

    @staticmethod
    def _drawing_context(record: ReconciliationRecord) -> str:
        lines = [
            f"Drawing: {record.drawing_number or 'unknown'} rev {record.revision or 'unknown'}",
            f"Submitted by: {record.submitted_by or 'unknown'}",
            "Signed off: " + (f"yes, by {record.signed_off_by}" if record.signed_off else "no"),
            f"Total balloons: {len(record.balloons)}",
            "",
            "Balloons (sheet/page, number, nominal value+unit, tolerance, GD&T, confidence + why, review status, notes):",
        ]
        for b in sorted(record.balloons, key=lambda x: (x.page, x.balloon_number)):
            v = b.reviewed if b.reviewed is not None else b.extracted
            gdt = (
                f"{v.gdt.symbol} {v.gdt.value} (mods={','.join(v.gdt.modifiers) or '-'}, datums={','.join(v.gdt.datums) or '-'})"
                if v.gdt
                else "-"
            )
            tol = (
                f"+{v.upper_tol}/{v.lower_tol}"
                if v.upper_tol is not None or v.lower_tol is not None
                else (v.tolerance_type.value if v.tolerance_type else "-")
            )
            corrected = " [reviewer-corrected]" if b.reviewed is not None and b.discrepancy else ""
            extra = ""
            if b.extracted.confidence_reason:
                extra += f" confidence_reason={b.extracted.confidence_reason!r}"
            if v.notes:
                extra += f" notes={v.notes!r}"
            if b.extracted.extraction_error:
                extra += f" ERROR={b.extracted.extraction_error!r}"
            lines.append(
                f"- p.{b.page} #{v.balloon_number}: value={v.nominal_value if v.nominal_value is not None else '-'} "
                f"{v.unit or ''} tol={tol} gdt={gdt} extraction_confidence={b.extracted.confidence:.2f} "
                f"status={b.status.value}{corrected}{extra}"
            )
        return "\n".join(lines)

    # ---------------------------------------------------------------- analysis (structured feedback)

    def analyze(self, record: ReconciliationRecord) -> AnalysisReport:
        """Structured feedback pass: deterministic rule findings first (src/analysis_rules.py),
        then Claude reviews/prioritizes them and can add anything the rules can't catch (odd
        phrasing, a callout that looks miskeyed). Falls back to rule-only findings if the AI call
        fails, so a Claude outage degrades the feature rather than breaking it."""
        rule_findings = run_checks(record)
        context = self._drawing_context(record)
        rule_findings_text = (
            "\n".join(f"- [{f.category.value}/{f.severity.value}] {f.summary}: {f.detail}" for f in rule_findings)
            or "(none found by the automated checks)"
        )

        user_message = (
            f"{context}\n\n"
            "Preliminary automated checks (already verified against the data above -- include these "
            "as findings unless you have a specific reason not to, and add any more of your own that "
            "these fixed rules can't catch, e.g. odd phrasing in a note, a value that looks miskeyed, "
            "or something inconsistent that isn't a simple field comparison):\n"
            f"{rule_findings_text}\n\n"
            f"Call {ANALYSIS_TOOL_NAME} with a short overall summary and the full findings list, "
            "covering missing information, incomplete data, inconsistencies between sheets, and "
            "common mistakes."
        )

        try:
            response = self._chat_client.chat_structured(
                system=_ANALYSIS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                tool_name=ANALYSIS_TOOL_NAME,
                tool_description=ANALYSIS_TOOL_DESCRIPTION,
                json_schema=_ANALYSIS_SCHEMA,
            )
        except ClaudeApiError:
            logger.exception("Analysis call to Claude failed; returning rule-based findings only")
            return AnalysisReport(
                job_id=record.job_id,
                generated_at=datetime.now(timezone.utc),
                summary="Automated checks only -- the AI review pass failed; see server logs for details.",
                findings=rule_findings,
            )

        findings = self._parse_findings(response.get("findings", []))
        if not findings:
            # Empty/malformed AI response (or Claude didn't call the tool) -- don't discard the
            # rule findings we already have just because the AI layer came back empty.
            findings = rule_findings

        summary = (response.get("summary") or "").strip() or "No summary returned by the AI review pass."
        return AnalysisReport(
            job_id=record.job_id, generated_at=datetime.now(timezone.utc), summary=summary, findings=findings
        )

    @staticmethod
    def _parse_findings(raw_findings: list[dict[str, Any]]) -> list[AnalysisFinding]:
        parsed: list[AnalysisFinding] = []
        for raw in raw_findings:
            try:
                parsed.append(AnalysisFinding(source=AnalysisFindingSource.AI, **raw))
            except Exception:  # noqa: BLE001 - one malformed finding shouldn't drop the whole report
                logger.warning("Dropping malformed analysis finding from Claude response: %r", raw)
        return parsed

    # ---------------------------------------------------------------- chat (free-form Q&A)

    def ask(self, record: ReconciliationRecord, message: str, history: list[dict[str, str]]) -> str:
        """Answers one free-form question grounded in this drawing's data. `history` is the prior
        turns of this conversation as sent by the client -- [{role, content}, ...] -- so the
        backend stays stateless across calls (see module docstring)."""
        context = self._drawing_context(record)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"Drawing data for grounding (not a message from the user):\n{context}"},
            {"role": "assistant", "content": "Understood -- I have the drawing's data. What would you like to know?"},
        ]
        for turn in history:
            role, content = turn.get("role"), turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        return self._chat_client.chat_text(system=CHAT_SYSTEM_PROMPT, messages=messages)
