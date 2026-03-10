"""Webread tool factory."""

from typing import TYPE_CHECKING

from src.tools.base import BaseTool, tool
from src.provider.web_read import WebReadProvider

if TYPE_CHECKING:
    from src.core.agent import AgentSession
    from src.utils.config import Config


def create_webread_tool(config: "Config") -> BaseTool | None:
    """Factory to create webread tool with injected config.

    Args:
        config: Config for accessing webread settings

    Returns:
        Tool function for web page reading or None if not configured
    """
    if not config.webread:
        return None

    provider = WebReadProvider.from_config(config)

    @tool(
        name="webread",
        description=(
            "Read and extract content from a web page. "
            "Returns the page content as markdown."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to read",
                }
            },
            "required": ["url"],
        },
    )
    async def webread(url: str, session: "AgentSession") -> str:
        """Read a web page and return markdown content.

        Args:
            url: The URL to read
            session: The agent session context

        Returns:
            Markdown content of the page or error message
        """

        result = await provider.read(url)

        if result.error:
            return f"Error reading {url}: {result.error}"

        return f"**{result.title}**\n\n{result.content}"

    return webread
