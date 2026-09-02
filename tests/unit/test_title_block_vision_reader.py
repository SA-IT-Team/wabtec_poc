from src.ai_clients import FakeChatCompletionClient
from src.exceptions import ClaudeApiError
from src.title_block_vision_reader import TITLE_BLOCK_TOOL_NAME, TitleBlockVisionReader


def test_returns_fields_from_a_successful_read():
    fake = FakeChatCompletionClient(canned_structured=[{"drawing_number": "CW-2045", "revision": "A"}])
    reader = TitleBlockVisionReader(fake)

    fields = reader.read(b"fake-png-bytes")

    assert fields.drawing_number == "CW-2045"
    assert fields.revision == "A"


def test_treats_empty_strings_as_none():
    fake = FakeChatCompletionClient(canned_structured=[{"drawing_number": "", "revision": ""}])
    reader = TitleBlockVisionReader(fake)

    fields = reader.read(b"fake-png-bytes")

    assert fields.drawing_number is None
    assert fields.revision is None


def test_returns_none_for_both_on_a_claude_api_error_rather_than_raising():
    fake = FakeChatCompletionClient(canned_structured=[ClaudeApiError("upstream 503")])
    reader = TitleBlockVisionReader(fake)

    fields = reader.read(b"fake-png-bytes")

    assert fields.drawing_number is None
    assert fields.revision is None


def test_sends_the_page_image_and_forces_the_title_block_tool():
    fake = FakeChatCompletionClient(canned_structured=[{"drawing_number": "CW-2045", "revision": "A"}])
    reader = TitleBlockVisionReader(fake)

    reader.read(b"fake-png-bytes")

    [call] = fake.chat_calls
    assert call["tool_name"] == TITLE_BLOCK_TOOL_NAME
    content_blocks = call["messages"][0]["content"]
    assert any(block["type"] == "image" for block in content_blocks)
    assert any(block["type"] == "text" for block in content_blocks)
