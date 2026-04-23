"""SSE parser + aggregate_stream invariants.

Two properties proved here:

  1. **Delta concatenation**: for a well-formed text-only stream, the
     concatenation of all ``response.output_text.delta`` payloads equals
     the final ``response.output_text.done.text`` value (and equals the
     returned ``AggregatedResponse.content``).

  2. **Tool-call aggregation**: a stream that emits a function_call
     produces exactly one ``AggregatedToolCall`` with the correct name,
     id, and arguments.

These are validated against the exact event names the live ChatGPT
backend emits (confirmed during end-to-end testing).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from mybot.provider.llm.responses import (
    AggregatedResponse,
    SSEEvent,
    aggregate_stream,
)


async def _feed(events: list[SSEEvent]) -> AsyncIterator[SSEEvent]:
    for e in events:
        yield e


# ---- 1: text-only delta concatenation ----------------------------------


async def test_delta_concat_equals_done_text() -> None:
    """Concatenated deltas match the authoritative `done.text`."""
    chunks = ["Hel", "lo", ", ", "world", "!"]
    full = "".join(chunks)

    events = [
        SSEEvent(type="response.output_text.delta", data={"delta": c})
        for c in chunks
    ]
    events.append(
        SSEEvent(type="response.output_text.done", data={"text": full})
    )

    result = await aggregate_stream(_feed(events))
    assert isinstance(result, AggregatedResponse)
    assert result.content == full
    assert result.tool_calls == []


async def test_missing_done_falls_back_to_concat() -> None:
    """If the stream omits `done`, content is the delta concat."""
    events = [
        SSEEvent(type="response.output_text.delta", data={"delta": "par"}),
        SSEEvent(type="response.output_text.delta", data={"delta": "t1"}),
    ]
    result = await aggregate_stream(_feed(events))
    assert result.content == "part1"


async def test_unknown_events_ignored() -> None:
    """Events our parser doesn't recognize must not affect content."""
    events = [
        SSEEvent(type="response.output_text.delta", data={"delta": "hi"}),
        SSEEvent(type="response.some_future_thing", data={"x": 1}),
        SSEEvent(type="response.output_text.done", data={"text": "hi"}),
    ]
    result = await aggregate_stream(_feed(events))
    assert result.content == "hi"


# ---- 2: tool-call aggregation (live event names) ----------------------


async def test_function_call_aggregation_live_event_names() -> None:
    """Using the event names the live ChatGPT backend actually emits.

    Sequence observed in production traffic:
      * response.output_item.added (registers the function_call item)
      * response.function_call_arguments.delta  (streams JSON arguments)
      * response.function_call_arguments.done   (finalizes arguments)
      * response.output_item.done               (commits the item)
    """
    item_id = "fc_abc123"
    call_id = "call_xyz789"

    events = [
        SSEEvent(
            type="response.output_item.added",
            data={
                "item": {
                    "id": item_id,
                    "type": "function_call",
                    "status": "in_progress",
                    "name": "bash",
                    "call_id": call_id,
                    "arguments": "",
                }
            },
        ),
        SSEEvent(
            type="response.function_call_arguments.delta",
            data={"item_id": item_id, "delta": '{"comm'},
        ),
        SSEEvent(
            type="response.function_call_arguments.delta",
            data={"item_id": item_id, "delta": 'and":"echo hi"}'},
        ),
        SSEEvent(
            type="response.function_call_arguments.done",
            data={
                "item_id": item_id,
                "arguments": '{"command":"echo hi"}',
            },
        ),
        SSEEvent(
            type="response.output_item.done",
            data={
                "item": {
                    "id": item_id,
                    "type": "function_call",
                    "status": "completed",
                    "name": "bash",
                    "call_id": call_id,
                    "arguments": '{"command":"echo hi"}',
                }
            },
        ),
    ]

    result = await aggregate_stream(_feed(events))

    assert result.content == ""  # no text deltas in this stream
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.id == call_id
    assert tc.name == "bash"
    assert tc.arguments == '{"command":"echo hi"}'


async def test_tool_calls_without_name_dropped() -> None:
    """An item that never received a name/`done` is dropped, not emitted
    with empty fields.

    This prevents malformed streams from surfacing half-baked tool calls
    to the caller.
    """
    events = [
        SSEEvent(
            type="response.function_call_arguments.delta",
            data={"item_id": "orphan", "delta": '{"x":1}'},
        ),
    ]
    result = await aggregate_stream(_feed(events))
    assert result.tool_calls == []
