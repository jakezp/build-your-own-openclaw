# Step 06: Web Tools

> Your agent meets the internet.

## Prerequisites

- Steps 00–05 done.
- A **Brave Search API key** (free tier available at [search.brave.com](https://api.search.brave.com/app/keys)). The tutorial uses Brave because it's cheap and doesn't require OAuth. Any HTTP search API would work with a new backend.

```bash
cd 06-web-tools
uv sync
```

Then add your Brave key to `default_workspace/config.user.yaml`:

```yaml
websearch:
  provider: brave
  api_key: your-brave-key-here

webread:
  provider: crawl4ai
```

Without these blocks in config, the web tools don't register. Graceful degradation — the agent still works, just without the internet.

## Why this step exists

Up to now, every tool your agent has is either a filesystem read, a filesystem write, or a shell command. If you ask "what's the weather in Seattle right now?", the model has to guess or admit it doesn't know.

Two new tools fix that:

- **`websearch`** — takes a query, returns a list of (title, URL, snippet) results.
- **`webread`** — takes a URL, returns the page content as markdown.

With both, the agent can now: search for something, pick a promising result, read that page, summarize. That's the core loop of any research-capable agent.

## The mental model

Two patterns worth naming:

### Pattern 1: provider-backed tools

Each web tool is the **thin public face** of a **provider** that does the actual work. Structure:

```
tools/
├── websearch_tool.py    # the @tool-decorated function — small
└── webread_tool.py

provider/
├── web_search/
│   ├── base.py          # abstract WebSearchProvider
│   └── brave.py         # BraveWebSearchProvider — the real implementation
└── web_read/
    ├── base.py
    └── crawl4ai.py
```

The tool is boring: build a schema, take args from the model, call the provider, format the result. The provider is where the real work happens: HTTP calls, API-specific quirks, pagination, rate limiting.

This shape lets us swap search backends. Want to use Google Custom Search instead of Brave? Add `provider/web_search/google.py`, no tool code changes.

### Pattern 2: optional tools via config

The tools register themselves ONLY if their provider is configured. Look at `_build_tools` in `agent.py`:

```python
registry = ToolRegistry.with_builtins()

if self.agent_def.allow_skills:
    skill_tool = create_skill_tool(self.skill_loader)
    if skill_tool:
        registry.register(skill_tool)

websearch_tool = create_websearch_tool(self.config)  # NEW
if websearch_tool:
    registry.register(websearch_tool)

webread_tool = create_webread_tool(self.config)     # NEW
if webread_tool:
    registry.register(webread_tool)

return registry
```

`create_websearch_tool(config)` returns `None` if `config.websearch` is missing. Same for `webread`. The agent quietly gets fewer tools; the model just doesn't see the missing ones in its schema.

This is intentional. Not every user has a Brave key. Not every agent should be allowed web access. Tools become **opt-in capability gates**.

## Key decisions

### Decision 1: two tools, not one

You could imagine a single `web` tool with a `mode: search | read` parameter. We split them because:

- **Descriptions stay focused.** A tool that "does two things depending on a mode" has a longer description and more chance for model confusion.
- **Schemas stay simple.** `websearch` takes `query`; `webread` takes `url`. Collapsing would mean `{mode: "search", query: "..."}` which is harder to call correctly.
- **Authorization can differ later.** In a production system you might allow an agent to search but not to read arbitrary URLs (because `webread` can be weaponized for SSRF). Separate tools = separate permissions.

### Decision 2: results are markdown strings, not structured data

Both tools return a string formatted with markdown. No JSON, no dataclass. Why?

Because the LLM's next input is just more conversation history. Whatever the tool returns ends up as `{"role": "tool", "content": "<whatever string>"}`. The model reads strings.

If we returned a dict, the tool layer would have to JSON-encode it. The model would then parse JSON back out. That's a round-trip tax for no gain. Keep it as formatted text.

### Decision 3: `webread` returns markdown, not HTML

HTML is 10x bigger than the equivalent markdown and half of it is cruft (nav bars, footers, ads). Feeding raw HTML to the model is wasteful and distracting.

`crawl4ai` (our default web-read provider) extracts the main content and converts it to markdown. The model sees the article, not the site chrome.

### Decision 4: `WebSearchProvider` and `WebReadProvider` as ABCs

Both are abstract bases:

```python
class WebSearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str) -> list[SearchResult]:
        ...
```

`BraveWebSearchProvider` subclasses it. If you want `GoogleWebSearchProvider`, write a new subclass. The factory function `create_websearch_tool` reads `config.websearch.provider` (a string like `"brave"`) and dispatches.

Same shape as `LLMProvider` in step 00 — consistent pattern.

## Read the code

### 1. `src/mybot/provider/web_search/base.py` + `brave.py`

The abstract base:

```python
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class WebSearchProvider(ABC):
    @staticmethod
    def from_config(config) -> "WebSearchProvider":
        if config.websearch.provider == "brave":
            return BraveWebSearchProvider(api_key=config.websearch.api_key)
        raise ValueError(f"Unknown websearch provider: {config.websearch.provider}")

    @abstractmethod
    async def search(self, query: str) -> list[SearchResult]:
        ...
```

`from_config` is the dispatch point. Adding a new backend means adding a branch here.

The Brave implementation uses `httpx` to call the Brave Search API, parses the JSON response, maps each hit into a `SearchResult`. ~50 lines.

### 2. `src/mybot/provider/web_read/base.py` + `crawl4ai.py`

Same shape. `WebReadProvider.read(url)` returns a `ReadResult(title, content, error)`. The Crawl4AI backend uses the `crawl4ai` library to fetch the URL, extract main content, convert to markdown.

### 3. `src/mybot/tools/websearch_tool.py`

The tool wrapper:

```python
def create_websearch_tool(config: "Config") -> BaseTool | None:
    if not config.websearch:
        return None

    provider = WebSearchProvider.from_config(config)

    @tool(
        name="websearch",
        description="Search the web for information...",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", ...}},
            "required": ["query"],
        },
    )
    async def websearch(query: str, session) -> str:
        results = await provider.search(query)
        if not results:
            return "No results found."
        output = []
        for i, r in enumerate(results, 1):
            output.append(f"{i}. **{r.title}**\n   {r.url}\n   {r.snippet}")
        return "\n\n".join(output)

    return websearch
```

The factory function pattern (same as `create_skill_tool` in step 02): returns `None` if unconfigured, a tool if configured. The tool closure captures the provider.

`webread_tool.py` has the same shape. Two small files, ~40 lines each.

### 4. `src/mybot/core/agent.py`

Only change: `_build_tools` calls the two new factories after the skill tool. `config.websearch` and `config.webread` are now optional pydantic fields on `Config` (pre-existing in this step's `utils/config.py`).

## Try it out

With a Brave key in your config:

```bash
uv run my-bot chat
```

```
You: What's the latest news about Python 3.13?
pickle: [calls websearch with "Python 3.13 latest news", gets results, summarizes]

You: Read the first result and tell me what's new in 3.13
pickle: [calls webread on the URL, gets markdown, summarizes]
```

Without a Brave key (or with `websearch:` removed from config), the same questions still work — the model falls back to its training-data knowledge or says it doesn't know.

## Exercises

1. **Swap the search backend.** Pick any HTTP search API (DuckDuckGo, SerpAPI, a local search engine). Implement `<new>_provider.py` subclassing `WebSearchProvider`. Add a branch in `from_config`. Update `config.websearch.provider`. Test without touching any tool code.

2. **Add a `max_results` parameter.** Extend the tool schema so the model can ask for "just the top 3." Thread it through to the provider. Notice: you're now exposing a provider-specific knob through a generic tool interface. That's a design smell; at what point do you split the tool?

3. **See the tool chain.** Ask a question that requires search → read → synthesize ("What is LangChain? Give me the gist from their main README."). Count the tool calls in the conversation log. Two at minimum (websearch, webread), possibly more if the model needs to iterate.

4. **Plumb SSRF safety.** `webread` takes arbitrary URLs. A malicious prompt could ask it to read `http://169.254.169.254/latest/meta-data/` (EC2 metadata service) or `file:///etc/passwd`. Add a URL allowlist/denylist in `crawl4ai.py`'s `read()`. This is the kind of safety layer a real agent framework needs.

## What breaks next

You now have a CLI agent that can chat, use tools, remember things, search the web, and manage its own context. Nothing obvious is missing for one-user-at-a-terminal.

What breaks when you want **more than one user** talking to the agent? When you want it to respond to Telegram messages, or Discord DMs, or a WebSocket connection?

A synchronous CLI loop blocks on `input()`. An event-driven architecture doesn't. Step 07 refactors the agent around an **event bus** — inbound events from any channel, outbound events from any worker, processed by a pool of agents. This is the shape everything from step 07 onward builds on.

## What's Next

[Step 07: Event-Driven Architecture](../07-event-driven/) — refactor around an event bus so the agent can listen to more than one input stream.
