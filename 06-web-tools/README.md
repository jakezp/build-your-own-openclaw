# Step 06: Web Tools - Access the Internet

Add web search and URL reading capabilities to the agent.

## Prerequisites

Same as Step 00, plus optional web API keys:

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# Edit config.user.yaml to add your API key and optionally:
# - websearch.api_key (Brave Search API key from https://brave.com/search/api/)
# - webread config (uses Crawl4AI, no API key needed)
```

## What We Will Build

### Architecture

```
AgentSession.chat() → Tool Call (websearch/webread)
                              │
                              ├─→ websearch → WebSearchProvider → Brave Search API
                              │
                              └─→ webread → WebReadProvider → Crawl4AI
```

### Key Components

- **WebSearchProvider**: Abstract base for search providers with `from_config()` factory
- **WebReadProvider**: Abstract base for web reading providers
- **Tools**: `websearch` and `webread` tools registered conditionally

## Key Changes

[src/provider/web_search/](src/provider/web_search/) - New module

```python
class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str

class WebSearchProvider(ABC):
    async def search(self, query: str) -> list[SearchResult]: ...
    @staticmethod
    def from_config(config: Config) -> WebSearchProvider: ...
```

[src/provider/web_read/](src/provider/web_read/) - New module

```python
class ReadResult(BaseModel):
    url: str
    title: str
    content: str  # Markdown
    error: str | None = None

class WebReadProvider(ABC):
    async def read(self, url: str) -> ReadResult: ...
```

[src/tools/websearch_tool.py](src/tools/websearch_tool.py) - New file

```python
def create_websearch_tool(config: Config) -> BaseTool | None:
    if not config.websearch:
        return None
    provider = WebSearchProvider.from_config(config)
    # Returns tool that searches and formats results as markdown
```

## How to Run

```bash
cd 06-web-tools
uv run your-own-bot chat

# You: Search the web for the latest news about AI
# Agent: [uses websearch tool]
# 1. **Latest AI News - TechCrunch**
#    https://techcrunch.com/...
#    Breaking developments in artificial intelligence...

# You: Read the content from https://example.com
# Agent: [uses webread tool]
# **Example Page Title**
#
# The page content in markdown format...
```

## What's Next

[Step 07: Event-Driven](../07-event-driven/) - The great refactor to event-based architecture
