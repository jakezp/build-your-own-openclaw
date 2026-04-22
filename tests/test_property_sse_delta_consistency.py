"""Property 8: SSE delta concatenation equals final text.

Validates: Requirements 3.5, 8.7.

For a well-formed Responses-API SSE stream consisting of N
``response.output_text.delta`` events followed by a
``response.output_text.done`` event carrying the full text, the
``aggregate_stream`` helper must return ``AggregatedResponse.content``
equal to the ``done.text`` value, and that value must equal the
concatenation of the delta strings.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from hypothesis import given, settings, strategies as st

from mybot.provider.llm.responses import (
    AggregatedResponse,
    SSEEvent,
    aggregate_stream,
)


_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs", "Cc"),
        blacklist_characters="\n\r",
    ),
    min_size=1,
    max_size=100,
)


def _partition(t: str, n: int) -> list[str]:
    """Split string ``t`` into exactly ``n`` non-empty chunks.

    Guarantees ``"".join(chunks) == t`` and ``len(chunks) == n``
    as long as ``len(t) >= n``.
    """
    assert len(t) >= n, (t, n)
    # Simple even partition, adjusted for leftover characters.
    size = len(t) // n
    rem = len(t) % n
    chunks: list[str] = []
    i = 0
    for k in range(n):
        extra = 1 if k < rem else 0
        end = i + size + extra
        chunks.append(t[i:end])
        i = end
    # All non-empty because size >= 1 or extra == 1 for the first `rem` slots;
    # the assertion above plus this math guarantees non-emptiness.
    assert all(c for c in chunks)
    assert "".join(chunks) == t
    return chunks


async def _make_stream(events: list[SSEEvent]) -> AsyncIterator[SSEEvent]:
    for e in events:
        yield e


def _build_events(text: str, n_chunks: int) -> tuple[list[SSEEvent], list[str]]:
    """Build N delta events + one done event from ``text``."""
    chunks = _partition(text, n_chunks)
    events: list[SSEEvent] = [
        SSEEvent(type="response.output_text.delta", data={"delta": c})
        for c in chunks
    ]
    events.append(
        SSEEvent(type="response.output_text.done", data={"text": text})
    )
    return events, chunks


@pytest.mark.asyncio
@given(
    text=_text,
    n_chunks=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=50, deadline=None)
async def test_delta_concat_equals_final_text(text: str, n_chunks: int) -> None:
    """concat(deltas) == done.text == aggregate_stream(stream).content."""
    # Ensure the partition is feasible: require at least as many chars as chunks.
    if len(text) < n_chunks:
        n_chunks = len(text)
    events, chunks = _build_events(text, n_chunks)

    result = await aggregate_stream(_make_stream(events))
    assert isinstance(result, AggregatedResponse)
    assert result.content == text
    assert "".join(chunks) == text
    # No tool calls in a text-only stream.
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_done_text_overrides_delta_concat_when_mismatched() -> None:
    """If ``done.text`` disagrees with the delta concat (defensive), the
    ``done`` value wins — it is the authoritative final text per backend.
    """
    events = [
        SSEEvent(type="response.output_text.delta", data={"delta": "wrong"}),
        SSEEvent(
            type="response.output_text.done", data={"text": "authoritative"}
        ),
    ]
    result = await aggregate_stream(_make_stream(events))
    assert result.content == "authoritative"


@pytest.mark.asyncio
async def test_missing_done_falls_back_to_delta_concat() -> None:
    """If the stream lacks a ``done`` event, ``content`` is the
    concatenation of deltas (defensive path).
    """
    events = [
        SSEEvent(type="response.output_text.delta", data={"delta": "hel"}),
        SSEEvent(type="response.output_text.delta", data={"delta": "lo"}),
    ]
    result = await aggregate_stream(_make_stream(events))
    assert result.content == "hello"


@pytest.mark.asyncio
async def test_unknown_events_are_ignored() -> None:
    """Forward-compat: unknown SSE event types must not affect content."""
    events = [
        SSEEvent(type="response.output_text.delta", data={"delta": "hi"}),
        SSEEvent(type="response.some_future_event", data={"x": 1}),
        SSEEvent(type="response.output_text.done", data={"text": "hi"}),
    ]
    result = await aggregate_stream(_make_stream(events))
    assert result.content == "hi"
    assert result.tool_calls == []
