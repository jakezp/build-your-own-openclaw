"""Base LLM provider abstraction."""

from dataclasses import dataclass
from typing import Any, Optional, cast

from litellm import acompletion, Choices
from litellm.types.completion import ChatCompletionMessageParam as Message

from src.utils.config import LLMConfig


@dataclass
class LLMToolCall:
    """
    A tool/function call from the LLM.

    Simplified adapter over litellm's ChatCompletionMessageToolCall
    which has nested structure (function.name, function.arguments).
    """

    id: str
    name: str
    arguments: str  # JSON string


class LLMProvider:
    """
    LLM provider using litellm for multi-provider support.

    Wraps litellm's acompletion for async chat calls.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ):
        """Initialize LLM provider.

        Args:
            model: Model name (e.g., "gpt-4", "claude-3-opus-20240229")
            api_key: API key for the provider
            api_base: Custom API endpoint (optional)
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            **kwargs: Additional provider-specific settings
        """
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._settings = kwargs

    @classmethod
    def from_config(cls, config: LLMConfig) -> "LLMProvider":
        """Create provider from LLMConfig.

        Args:
            config: LLMConfig object

        Returns:
            LLMProvider instance
        """
        return cls(
            model=config.model,
            api_key=config.api_key,
            api_base=config.api_base,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

    async def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> tuple[str, list[LLMToolCall]]:
        """
        Send a chat request to the LLM.

        Default implementation using litellm. Subclasses can override
        if provider-specific behavior is needed.
        """
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "api_key": self.api_key,
        }

        if self.api_base:
            request_kwargs["api_base"] = self.api_base
        if tools:
            request_kwargs["tools"] = tools
        request_kwargs.update(kwargs)

        response = await acompletion(**request_kwargs)

        message = cast(Choices, response.choices[0]).message

        return (
            message.content or "",
            [
                LLMToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                )
                for tc in (message.tool_calls or [])
            ],
        )
