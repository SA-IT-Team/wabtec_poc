"""Shared retry policy for outbound Azure SDK calls.

architecture-poc.md §2.2: "Retry with exponential backoff -- wraps both the Document Intelligence
and Azure OpenAI SDK calls for 429/503 responses; 3 attempts, base 2s, jittered."
"""
from __future__ import annotations

from typing import Type

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter


def azure_retry(exception_types: tuple[Type[BaseException], ...]):
    """Returns a tenacity decorator: 3 attempts, exponential backoff from 2s, jittered.

    Only retries on the given exception types (typically transient upstream errors); anything
    else propagates immediately. Re-raises the original exception once attempts are exhausted.
    """
    return retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=2, max=10),
        retry=retry_if_exception_type(exception_types),
    )
