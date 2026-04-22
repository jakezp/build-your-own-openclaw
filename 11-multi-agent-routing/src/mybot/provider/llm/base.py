"""Responses-API-backed LLM provider (tool-aware variant)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

from mybot.provider.llm.oauth import ChatGPTOAuth
from mybot.provider.llm.responses import (
    ResponsesClient,
    ResponsesRequest,
    aggregate_stream,
)

if TYPE_CHECKING:
    from mybot.utils.config import LLMConfig


@dataclass
class LLMToolCall:
    """A tool/function call from the LLM."""

    id: str
    name: str
    arguments: str  # JSON string


def _translate_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Split Chat-Completions messages into (instructions, Responses input).

    The Responses API takes a top-level ``instructions`` string plus an
    ``input`` list of role/content items and function_call / function_call_output
    items. Chat-Completions packs the system prompt as the first message and
    encodes tool calls inline on assistant messages, so we translate:

    * ``role: system`` messages → concatenated into ``instructions``.
    * ``role: assistant`` with ``tool_calls`` → one plain assistant message
      (if content non-empty) plus one ``function_call`` item per tool call.
    * ``role: tool`` → ``function_call_output`` item keyed by ``tool_call_id``.
    * Everything else (user, assistant without tool calls) → ``{role, content}``.
    """
    instructions_parts: list[str] = []
    input_items: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            content = m.get("content") or ""
            if isinstance(content, str):
                instructions_parts.append(content)
            continue
        if role == "assistant" and m.get("tool_calls"):
            content = m.get("content") or ""
            if content:
                input_items.append({"role": "assistant", "content": content})
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", ""),
                    }
                )
            continue
        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": m.get("tool_call_id", ""),
                    "output": m.get("content", ""),
                }
            )
            continue
        # user / assistant-without-tool-calls
        input_items.append(
            {
                "role": role or "user",
                "content": m.get("content") or "",
            }
        )
    instructions = (
        "\n\n".join(p for p in instructions_parts if p)
        or "You are a helpful assistant."
    )
    return instructions, input_items


def _translate_tools(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Chat-Completions tool schema → Responses API tool schema.

    Chat-Completions nests the function definition under a ``function`` key;
    Responses flattens it. Schemas already in Responses shape (no ``function``
    key) pass through untouched.
    """
    if not tools:
        return None
    out: list[dict[str, Any]] = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            fn = t["function"]
            out.append(
                {
                    "type": "function",
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                }
            )
        else:
            out.append(t)
    return out


class LLMProvider:
    """Responses-API client for the ChatGPT subscription backend."""

    def __init__(
        self,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._settings = kwargs
        self._oauth = ChatGPTOAuth()
        self._client = ResponsesClient()

    @classmethod
    def from_config(cls, config: "LLMConfig") -> "LLMProvider":
        return cls(
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

    async def _resolve_credential(self) -> tuple[str, str]:
        """Return (access_token, account_id) from the shared Token_Store."""
        token = await self._oauth.access_token()
        account_id = await self._oauth.account_id()
        return token, account_id

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> tuple[str, list[LLMToolCall]]:
        """Send a chat turn to the Responses API and return (content, tool_calls)."""
        access_token, account_id = await self._resolve_credential()
        instructions, input_items = _translate_messages(messages)
        resp_tools = _translate_tools(tools)
        request = ResponsesRequest(
            model=self.model,
            instructions=instructions,
            input=input_items,
            tools=resp_tools,
        )
        events = self._client.stream(
            request,
            access_token=access_token,
            account_id=account_id,
        )
        aggregated = await aggregate_stream(events)
        return (
            aggregated.content,
            [
                LLMToolCall(id=tc.id, name=tc.name, arguments=tc.arguments)
                for tc in aggregated.tool_calls
            ],
        )
