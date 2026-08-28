"""Unit tests for ChatAssistant -- the chatbot's structured analysis pass and free-form Q&A.
Uses FakeChatCompletionClient throughout, no network calls."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.ai_clients import FakeChatCompletionClient
from src.chat_assistant import ANALYSIS_TOOL_NAME, ChatAssistant
from src.exceptions import ClaudeApiError
from src.models import (
    AnalysisFindingCategory,
    AnalysisFindingSource,
    BalloonReviewRecord,
    BalloonReviewStatus,
    ExtractedBalloon,
    ReconciliationRecord,
)


def _balloon(number: int, page: int = 1, **overrides) -> ExtractedBalloon:
    defaults = dict(balloon_number=number, page=page, nominal_value=25.4, unit="mm", confidence=0.95)
    defaults.update(overrides)
    return ExtractedBalloon(**defaults)


def _record(*balloons: ExtractedBalloon, **record_overrides) -> ReconciliationRecord:
    reviews = [
        BalloonReviewRecord(page=b.page, balloon_number=b.balloon_number, extracted=b, status=BalloonReviewStatus.PENDING)
        for b in balloons
    ]
    defaults = dict(job_id="job-1", drawing_number="DWG-1", revision="A", balloons=reviews, created_at=datetime.now(timezone.utc))
    defaults.update(record_overrides)
    return ReconciliationRecord(**defaults)


class TestAnalyze:
    def test_returns_ai_findings_merged_with_summary(self):
        fake = FakeChatCompletionClient(
            canned_structured=[
                {
                    "summary": "Mostly clean; one balloon needs a second look.",
                    "findings": [
                        {
                            "category": "incomplete_data",
                            "severity": "warning",
                            "summary": "Balloon 1 has low confidence",
                            "detail": "Confidence 0.40 is below the reliability threshold.",
                            "balloon_refs": [{"page": 1, "balloon_number": 1}],
                        }
                    ],
                }
            ]
        )
        assistant = ChatAssistant(fake)
        record = _record(_balloon(1, confidence=0.4))

        report = assistant.analyze(record)

        assert report.job_id == "job-1"
        assert report.summary == "Mostly clean; one balloon needs a second look."
        assert len(report.findings) == 1
        assert report.findings[0].source == AnalysisFindingSource.AI
        assert report.findings[0].category == AnalysisFindingCategory.INCOMPLETE_DATA

    def test_hands_rule_findings_to_claude_as_grounding(self):
        fake = FakeChatCompletionClient(canned_structured=[{"summary": "ok", "findings": []}])
        assistant = ChatAssistant(fake)
        record = _record(_balloon(1, confidence=0.4))  # triggers a rule finding

        assistant.analyze(record)

        [call] = fake.chat_calls
        assert call["tool_name"] == ANALYSIS_TOOL_NAME
        user_content = call["messages"][0]["content"]
        assert "low confidence" in user_content or "incomplete_data" in user_content

    def test_falls_back_to_rule_findings_on_claude_failure(self):
        fake = FakeChatCompletionClient(canned_structured=[ClaudeApiError("upstream 503")])
        assistant = ChatAssistant(fake)
        record = _record(_balloon(1, confidence=0.4))  # a rule finding exists

        report = assistant.analyze(record)

        assert "AI review pass failed" in report.summary
        assert len(report.findings) == 1
        assert report.findings[0].source.value == "rule"

    def test_falls_back_to_rule_findings_when_ai_returns_no_findings(self):
        fake = FakeChatCompletionClient(canned_structured=[{"summary": "looks fine", "findings": []}])
        assistant = ChatAssistant(fake)
        record = _record(_balloon(1, confidence=0.4))  # a rule finding exists

        report = assistant.analyze(record)

        assert len(report.findings) == 1
        assert report.findings[0].source.value == "rule"

    def test_drops_malformed_findings_without_raising(self):
        fake = FakeChatCompletionClient(
            canned_structured=[
                {
                    "summary": "ok",
                    "findings": [
                        {"category": "not_a_real_category", "severity": "warning", "summary": "x", "detail": "y"},
                    ],
                }
            ]
        )
        assistant = ChatAssistant(fake)
        record = _record(_balloon(1))  # no rule findings, so fallback list is empty too

        report = assistant.analyze(record)

        assert report.findings == []


class TestAsk:
    def test_returns_claudes_text_reply(self):
        fake = FakeChatCompletionClient(canned_text=["Balloon 3 has a bilateral tolerance of ±0.05mm."])
        assistant = ChatAssistant(fake)
        record = _record(_balloon(3))

        reply = assistant.ask(record, "What's the tolerance on balloon 3?", history=[])

        assert reply == "Balloon 3 has a bilateral tolerance of ±0.05mm."

    def test_includes_drawing_data_and_history_in_the_call(self):
        fake = FakeChatCompletionClient(canned_text=["ok"])
        assistant = ChatAssistant(fake)
        record = _record(_balloon(1, unit="mm"))

        assistant.ask(
            record,
            "and the second one?",
            history=[{"role": "user", "content": "what unit is balloon 1?"}, {"role": "assistant", "content": "mm"}],
        )

        [call] = fake.chat_calls
        contents = [m["content"] for m in call["messages"]]
        assert any("DWG-1" in c for c in contents)
        assert any("what unit is balloon 1?" in c for c in contents)
        assert contents[-1] == "and the second one?"

    def test_ignores_malformed_history_entries(self):
        fake = FakeChatCompletionClient(canned_text=["ok"])
        assistant = ChatAssistant(fake)
        record = _record(_balloon(1))

        # should not raise even with junk in history
        reply = assistant.ask(record, "hi", history=[{"role": "system", "content": "ignored"}, {"nonsense": True}])

        assert reply == "ok"

    def test_propagates_claude_api_error(self):
        fake = FakeChatCompletionClient(canned_text=[ClaudeApiError("timeout")])
        assistant = ChatAssistant(fake)
        record = _record(_balloon(1))

        with pytest.raises(ClaudeApiError):
            assistant.ask(record, "hi", history=[])
