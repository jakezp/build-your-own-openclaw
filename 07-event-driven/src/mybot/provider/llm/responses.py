"""ChatGPT Responses API client: pinned endpoint, typed builder, SSE stream.

This module is the single place the tutorial owns the low-level HTTP call to
``chatgpt.com/backend-api/codex/responses``. It is intentionally self-contained
so a learner can read it once in step 00 and skim-verify it in later steps
(the file is copied byte-identically across all 18 steps).

Scope is deliberately narrow:

1. Pinned constants: endpoint URL, originator header, required body invariants.
2. ``ResponsesRequest`` — typed builder for the POST body.
3. ``ResponsesClient`` — async httpx client that streams SSE events.
4. A small SSE parser (no external SSE library — ``httpx.Response.aiter_lines``
   plus the SSE framing rules is enough).
5. ``aggregate_stream`` — collapses an SSE event stream into final content
   plus tool calls, the shape ``LLMProvider.chat()`` returns.

Credential resolution lives in ``oauth.py`` / ``base.py``; this file only
knows how to speak the Responses API wire protocol given an access token
and an account id.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Pinned constants (confirmed against openai/codex Rust source).
# ---------------------------------------------------------------------------

CHATGPT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
# Matches codex-rs/login/src/auth/default_client.rs::DEFAULT_ORIGINATOR,
# same value as oauth.py's CHATGPT_ORIGINATOR.
CHATGPT_ORIGINATOR = "codex_cli_rs"

# Required-by-backend invariants. The Responses API rejects requests that
# set ``stream=False`` or ``store=True`` for this client, so we pin both.
REQUIRED_STREAM = True
REQUIRED_STORE = False


# ---------------------------------------------------------------------------
# Data classes.
# ---------------------------------------------------------------------------


@dataclass
class ResponsesRequest:
    """Typed builder for a Responses API POST body.

    ``to_body()`` is the single place request shape is expressed, so every
    request-shape test has one function to validate against. ``stream`` and
    ``store`` are pinned here — callers cannot override them.
    """

    model: str
    instructions: str
    input: list[dict[str, Any]]  # role/content messages or function_call items
    tools: list[dict[str, Any]] | None = None

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": self.instructions,
            "input": self.input,
            "stream": REQUIRED_STREAM,
            "store": REQUIRED_STORE,
        }
        if self.tools:
            body["tools"] = self.tools
        return body


@dataclass
class SSEEvent:
    """A single parsed SSE event from the Responses API stream.

    ``type`` is the SSE ``event:`` field (e.g. ``response.output_text.delta``).
    ``data`` is the JSON-decoded ``data:`` payload. Unknown/malformed data
    payloads decode to an empty dict so the stream never raises mid-flight.
    """

    type: str
    data: dict[str, Any]


@dataclass
class AggregatedToolCall:
    """A single tool call pulled out of an SSE event stream."""

    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class AggregatedResponse:
    """Final collapsed result of an SSE event stream."""

    content: str
    tool_calls: list[AggregatedToolCall] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------


class ResponsesAPIError(RuntimeError):
    """Raised on non-2xx HTTP responses or ``response.error`` SSE events.

    ``detail`` is truncated to 500 chars at raise sites so any accidental
    token echo in a backend error body is bounded.
    """

    def __init__(self, status: int, detail: str):
        super().__init__(f"Responses API HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------
# SSE parsing.
# ---------------------------------------------------------------------------


async def _iter_sse(resp: httpx.Response) -> AsyncIterator[SSEEvent]:
    """Yield ``SSEEvent``s parsed from an httpx streaming response.

    SSE framing rules:
      * ``event: <type>`` sets the type for the pending event.
      * ``data: <json>`` appends to the pending data buffer (multi-line
        payloads are joined with ``\\n``).
      * A blank line emits the pending event and resets state.
      * Lines starting with ``:`` are comments and are skipped.
    """
    event_type: str | None = None
    data_buf: list[str] = []
    async for line in resp.aiter_lines():
        if line == "":
            if event_type is not None and data_buf:
                raw = "\n".join(data_buf)
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {}
                yield SSEEvent(type=event_type, data=data)
            event_type = None
            data_buf = []
            continue
        if line.startswith(":"):
            continue  # SSE comment
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_buf.append(line[len("data:"):].lstrip())


# ---------------------------------------------------------------------------
# ResponsesClient.
# ---------------------------------------------------------------------------


class ResponsesClient:
    """Async client for ``chatgpt.com/backend-api/codex/responses``."""

    def __init__(self, timeout: float = 120.0):
        self._timeout = timeout

    async def stream(
        self,
        request: ResponsesRequest,
        *,
        access_token: str,
        account_id: str,
    ) -> AsyncIterator[SSEEvent]:
        """POST the request and yield parsed SSE events.

        Raises ``ResponsesAPIError`` on non-2xx responses with the body
        truncated to 500 chars so any accidental token echo is bounded.
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "ChatGPT-Account-Id": account_id,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "originator": CHATGPT_ORIGINATOR,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                CHATGPT_RESPONSES_URL,
                headers=headers,
                json=request.to_body(),
            ) as resp:
                if resp.status_code // 100 != 2:
                    body = (await resp.aread()).decode(
                        "utf-8", errors="replace"
                    )
                    raise ResponsesAPIError(
                        status=resp.status_code,
                        detail=body[:500],
                    )
                async for event in _iter_sse(resp):
                    yield event


# ---------------------------------------------------------------------------
# Event aggregation.
# ---------------------------------------------------------------------------


async def aggregate_stream(
    events: AsyncIterator[SSEEvent],
) -> AggregatedResponse:
    """Collapse an SSE event stream into final content + tool calls.

    Handled event types (names confirmed against live ChatGPT backend):
      * ``response.output_text.delta`` — accumulate ``delta`` into text.
      * ``response.output_text.done`` — authoritative final text; if
        absent, fall back to the concatenated deltas.
      * ``response.output_item.added`` — when the item is a
        ``function_call``, register it with its ``name`` and ``call_id``
        keyed by the item's ``id``.
      * ``response.function_call_arguments.delta`` — accumulate
        arguments for the item looked up by ``item_id``.
      * ``response.function_call_arguments.done`` — authoritative final
        arguments string for the item looked up by ``item_id``.
      * ``response.output_item.done`` — when the item is a
        ``function_call``, commit its final ``arguments`` and ``name``.
      * ``response.error`` — raise ``ResponsesAPIError(status=0, ...)``.

    Unknown event types are ignored for forward compatibility.
    """
    text_deltas: list[str] = []
    final_text: str | None = None
    # item_id -> {"id": call_id, "name": ..., "arguments": ...}
    tool_calls: dict[str, dict[str, Any]] = {}

    async for evt in events:
        t = evt.type
        d = evt.data
        if t == "response.output_text.delta":
            delta = d.get("delta") or ""
            if delta:
                text_deltas.append(delta)
        elif t == "response.output_text.done":
            final_text = d.get("text") or "".join(text_deltas)
        elif t == "response.output_item.added":
            item = d.get("item") or {}
            if item.get("type") == "function_call":
                item_id = item.get("id") or ""
                if item_id:
                    tool_calls[item_id] = {
                        "id": item.get("call_id") or item_id,
                        "name": item.get("name") or "",
                        "arguments": item.get("arguments") or "",
                    }
        elif t == "response.function_call_arguments.delta":
            item_id = d.get("item_id") or d.get("call_id") or ""
            if item_id:
                tc = tool_calls.setdefault(
                    item_id,
                    {"id": item_id, "name": "", "arguments": ""},
                )
                tc["arguments"] = (tc.get("arguments") or "") + (
                    d.get("delta") or ""
                )
        elif t == "response.function_call_arguments.done":
            item_id = d.get("item_id") or d.get("call_id") or ""
            if item_id and "arguments" in d:
                tc = tool_calls.setdefault(
                    item_id, {"id": item_id, "name": "", "arguments": ""}
                )
                # Final arguments string from the backend — authoritative.
                tc["arguments"] = d["arguments"]
        elif t == "response.output_item.done":
            item = d.get("item") or {}
            if item.get("type") == "function_call":
                item_id = item.get("id") or ""
                if item_id:
                    tc = tool_calls.setdefault(
                        item_id, {"id": item_id, "name": "", "arguments": ""}
                    )
                    tc["id"] = item.get("call_id") or tc.get("id") or item_id
                    if item.get("name"):
                        tc["name"] = item["name"]
                    if item.get("arguments") is not None:
                        tc["arguments"] = item["arguments"]
        # ---- Legacy / alternate event names (carried for forward-compat) ----
        elif t == "response.function_call.arguments.delta":
            item_id = d.get("item_id") or d.get("call_id") or ""
            if item_id:
                tc = tool_calls.setdefault(
                    item_id, {"id": item_id, "name": "", "arguments": ""}
                )
                tc["arguments"] = (tc.get("arguments") or "") + (
                    d.get("delta") or ""
                )
        elif t == "response.function_call.done":
            item_id = (
                d.get("item_id") or d.get("call_id") or d.get("id") or ""
            )
            if not item_id:
                continue
            tc = tool_calls.setdefault(
                item_id, {"id": item_id, "name": "", "arguments": ""}
            )
            if "name" in d:
                tc["name"] = d["name"]
            if "arguments" in d:
                tc["arguments"] = d["arguments"]
        elif t == "response.error":
            raise ResponsesAPIError(
                status=0,
                detail=(d.get("error") or d.get("message") or "unknown"),
            )
        # Unknown event types are ignored (forward-compat).

    content = final_text if final_text is not None else "".join(text_deltas)
    return AggregatedResponse(
        content=content,
        tool_calls=[
            AggregatedToolCall(
                id=tc.get("id", item_id),
                name=tc.get("name", ""),
                arguments=tc.get("arguments", ""),
            )
            for item_id, tc in tool_calls.items()
            if tc.get("name")  # drop entries we never got a name for
        ],
    )
