# 步骤 06：Web 工具

> 你的智能体想看看更大的世界。
> 归根结底，它们只是两个新工具。

## 前置条件

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
# 取消注释 websearch 和 webread 部分
# 添加你的 websearch api 密钥
```

## 这节做什么

LLM 懂 Python，但不知道昨天 PyPI 上发了什么新包。加两个工具让它能搜网页、读 URL。

<img src="06-web-tools.svg" align="center" width="100%" />

## 关键组件

- **WebSearchProvider**：网络搜索提供商。
- **WebReadProvider**：网页阅读提供商。
- **Tools**：`websearch` 和 `webread` 工具。



[src/mybot/provider/web_search/](src/mybot/provider/web_search/)

```python
class WebSearchProvider(ABC):
    async def search(self, query: str) -> list[SearchResult]: ...
```

[src/mybot/provider/web_read/](src/mybot/provider/web_read/)

```python
class WebReadProvider(ABC):
    async def read(self, url: str) -> ReadResult: ...
```

[src/mybot/tools/websearch_tool.py](src/mybot/tools/websearch_tool.py)

```python
@tool(...)
async def websearch(query: str, session: "AgentSession") -> str:
    results = await provider.search(query)

    if not results:
        return "No results found."
    output = []
    for i, r in enumerate(results, 1):
        output.append(f"{i}. **{r.title}**\n   {r.url}\n   {r.snippet}")
    return "\n\n".join(output)
```

[src/mybot/tools/webread_tool.py](src/mybot/tools/webread_tool.py)

```python
@tool(...)
async def webread(url: str, session: "AgentSession") -> str:
    result = await provider.read(url)
    if result.error:
        return f"Error reading {url}: {result.error}"

    return f"**{result.title}**\n\n{result.content}"
```

## 试一试

```bash
cd 06-web-tools
uv run my-bot chat

# You: What is pickle bot? search online please.
# pickle: Based on my search, there are actually a few different things called "Pickle Bot":

# ### 1. **Pickle Robot Company** 🤖
# ### 2. **Pickle Bot (Discord Bot)** 💬
# ### 3. **pickle-bot (GitHub)** 🐱
# An open-source project described as:
# - "Your own AI assistant, speak like a cat"
# - "Pickle is a standard little cat"
# - A customizable AI assistant that you can name, talk to, and teach

# The GitHub version sounds like it could be related to me - a cat-speaking AI assistant! 😺

# Which one were you curious about?
```

## 下一步

[步骤 07：事件驱动](../07-event-driven/) - 重构为基于事件的架构
