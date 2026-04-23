"""LLM provider primitives: OAuth + Responses API client.

Step 000 only exposes the OAuth + SSE transport. The agent, session,
and LLMProvider class are introduced in later steps.
"""

from mybot.provider.llm.oauth import ChatGPTOAuth, OAuthCredentials, TokenStore
from mybot.provider.llm.responses import (
    CHATGPT_ORIGINATOR,
    CHATGPT_RESPONSES_URL,
    AggregatedResponse,
    AggregatedToolCall,
    ResponsesAPIError,
    ResponsesClient,
    ResponsesRequest,
    SSEEvent,
    aggregate_stream,
)

__all__ = [
    "ChatGPTOAuth",
    "OAuthCredentials",
    "TokenStore",
    "CHATGPT_ORIGINATOR",
    "CHATGPT_RESPONSES_URL",
    "AggregatedResponse",
    "AggregatedToolCall",
    "ResponsesAPIError",
    "ResponsesClient",
    "ResponsesRequest",
    "SSEEvent",
    "aggregate_stream",
]
