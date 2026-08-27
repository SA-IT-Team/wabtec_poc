from src.ai_clients import FakeChatCompletionClient
from src.exceptions import ClaudeApiError
from src.extraction_orchestrator import ExtractionOrchestrator, VisionGroundedExtractionStrategy
from src.preprocessor import PageImage


def _page() -> PageImage:
    return PageImage(page_number=1, png_bytes=b"fake-png", dpi=300, width_px=100, height_px=100)


def test_extract_page_returns_valid_balloons_on_first_try(sample_layout, sample_extraction_response):
    chat_client = FakeChatCompletionClient([sample_extraction_response])
    orchestrator = ExtractionOrchestrator(VisionGroundedExtractionStrategy(chat_client))
    candidates = [{"balloon_number": 12}, {"balloon_number": 13}]

    balloons = orchestrator.extract_page(_page(), sample_layout, candidates)

    assert [b.balloon_number for b in balloons] == [12, 13]
    assert balloons[0].nominal_value == 25.4
    assert balloons[1].gdt.symbol == "position"
    assert len(chat_client.calls) == 1


def test_repair_loop_recovers_from_one_malformed_response(sample_layout, sample_extraction_response):
    malformed = {"balloons": [{"balloon_number": "not-a-number"}]}  # fails schema validation
    chat_client = FakeChatCompletionClient([malformed, sample_extraction_response])
    strategy = VisionGroundedExtractionStrategy(chat_client, max_repair_attempts=1)
    orchestrator = ExtractionOrchestrator(strategy)

    balloons = orchestrator.extract_page(_page(), sample_layout, [])

    assert len(chat_client.calls) == 2
    assert [b.balloon_number for b in balloons] == [12, 13]
    # the repair re-prompt should include the validation error for the model to correct
    assert "invalid" in chat_client.calls[1]["layout_text"].lower()


def test_gives_up_after_max_repair_attempts_and_falls_back_to_detected_candidates(sample_layout):
    always_malformed = [{"balloons": [{"balloon_number": "x"}]}, {"balloons": [{"balloon_number": "y"}]}]
    chat_client = FakeChatCompletionClient(always_malformed)
    strategy = VisionGroundedExtractionStrategy(chat_client, max_repair_attempts=1)
    orchestrator = ExtractionOrchestrator(strategy)
    candidates = [{"balloon_number": 12}, {"balloon_number": 13}]

    # FR-10: even though the model never returned usable data, every detected candidate still
    # gets a row (confidence 0, extraction_error set) rather than silently vanishing.
    balloons = orchestrator.extract_page(_page(), sample_layout, candidates)

    assert len(chat_client.calls) == 2  # initial attempt + 1 repair, then orchestrator stops asking
    assert [b.balloon_number for b in balloons] == [12, 13]
    assert all(b.confidence == 0.0 for b in balloons)
    assert all(b.extraction_error for b in balloons)


def test_transport_failure_propagates_immediately_without_repair(sample_layout):
    chat_client = FakeChatCompletionClient([ClaudeApiError("service unavailable")])
    orchestrator = ExtractionOrchestrator(VisionGroundedExtractionStrategy(chat_client, max_repair_attempts=2))

    try:
        orchestrator.extract_page(_page(), sample_layout, [])
        assert False, "expected ClaudeApiError to propagate"
    except ClaudeApiError:
        pass

    assert len(chat_client.calls) == 1  # no repair retries for a transport failure
