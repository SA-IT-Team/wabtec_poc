"""TitleBlockVisionReader: reads drawing number and revision directly from the page image via the
extraction LLM, as a fallback when TitleBlockExtractor's OCR-line regex heuristic (src/title_block_
extractor.py) finds nothing.

Why this exists: real title blocks vary far more than a flattened-OCR-line regex can reliably keep
up with -- label wording ("DWG. NO." vs. "DRAWING NUMBER" vs. no label at all), whether a REV field
is even present, and critically, *how Document Intelligence happens to segment the title block into
OCR lines* (one line per cell vs. one line per row vs. everything run together), which varies by
drawing and isn't something this app controls. Chasing each new real-world layout with another regex
tweak doesn't converge -- confirmed the hard way across several real drawings. The extraction LLM is
already looking at the rendered page image for balloon extraction; asking it to also read the title
block, with vision, sidesteps the whole "which line segmentation did OCR choose this time" problem.

Only invoked when the regex heuristic comes up empty for at least one field, and only against the
first page -- not unconditionally on every page -- so it adds at most one extra Claude call per
drawing on top of the ones extraction already makes, not one per page.
"""
from __future__ import annotations

import logging

from src.ai_clients import IChatCompletionClient, decode_image_b64
from src.exceptions import ClaudeApiError
from src.title_block_extractor import TitleBlockFields

logger = logging.getLogger(__name__)

TITLE_BLOCK_TOOL_NAME = "record_title_block"
TITLE_BLOCK_TOOL_DESCRIPTION = "Records the drawing number and revision read from the title block."

SYSTEM_PROMPT = (
    "You are reading the title block of a mechanical engineering drawing page. Report the drawing "
    "number (sometimes labeled DWG. NO., DRAWING NUMBER, PART NO., PART NUMBER, or similar) and "
    "the revision (sometimes labeled REV, REVISION, or shown as a single letter/number in its own "
    "REV column) exactly as printed in the title block. If either isn't visible on this page, or "
    "this drawing doesn't have one (many drawings have no revision field at all), set it to null -- "
    "never guess or infer a value that isn't actually printed."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "drawing_number": {
            "type": ["string", "null"],
            "description": "The drawing/part number exactly as printed, or null if not visible.",
        },
        "revision": {
            "type": ["string", "null"],
            "description": "The revision letter/number exactly as printed, or null if not visible.",
        },
    },
    "required": ["drawing_number", "revision"],
}


class TitleBlockVisionReader:
    def __init__(self, chat_client: IChatCompletionClient):
        self._chat = chat_client

    def read(self, image_bytes: bytes) -> TitleBlockFields:
        """Never raises -- a failed vision read just means the fields stay whatever the regex
        heuristic already found (possibly still None), same as if this fallback didn't exist."""
        image_b64 = decode_image_b64(image_bytes)
        try:
            response = self._chat.chat_structured(
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"Call {TITLE_BLOCK_TOOL_NAME} with this page's title block fields.",
                            },
                            {
                                "type": "image",
                                "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
                            },
                        ],
                    }
                ],
                tool_name=TITLE_BLOCK_TOOL_NAME,
                tool_description=TITLE_BLOCK_TOOL_DESCRIPTION,
                json_schema=_SCHEMA,
            )
        except ClaudeApiError:
            logger.exception("Title block vision read failed; leaving drawing_number/revision unset")
            return TitleBlockFields(drawing_number=None, revision=None)

        return TitleBlockFields(
            drawing_number=response.get("drawing_number") or None,
            revision=response.get("revision") or None,
        )
