# Step 01: Tools

> Your agent can now do things, not just talk about them.

## Prerequisites

- [`000-oauth/`](../000-oauth/) completed.
- [`00-chat-loop/`](../00-chat-loop/) read and understood.

```bash
cd 01-tools
uv sync
```

## Why this step exists

The chat loop from step 00 has a fatal flaw: it can only emit words. Ask it to "list the files in my Downloads folder" and the best you get is a well-meaning guess. It has no sensors and no actuators. It's an opinion-machine.

Real agents take **actions**. They read files, run commands, query APIs, modify state in the world. The mechanism that bridges "what the model says" and "what happens in reality" is called a **tool** (in OpenAI-land; Anthropic calls them "functions," Google calls them "function calling," but they're the same thing). The model declares it wants to call a tool; your code executes that tool; you show the model the result; the model decides what to say or do next.

This step introduces four tools — `read`, `write`, `edit`, `bash` — and the registry that plugs them in. From now on, the agent can actually do work.

## The mental model

A tool call is a **structured request from the model** that looks like this:

```
"Please call the function `bash` with argument {command: 'ls ~/Downloads'}."
```

Not literal English — that's encoded in a JSON object on the wire. But that's the shape. The agent's side of the dance has three new jobs:

1. **Advertise.** Tell the model which tools exist by sending a *schema* with every chat request (the tool's name, its description, and a JSON Schema for its arguments).
2. **Execute.** When the model's reply comes back with tool calls instead of (or alongside) text, look up each tool by name, run it with the provided arguments, capture the output.
3. **Feed back.** Append the tool's output to the conversation history as a `{"role": "tool", ...}` message and hand the whole history back to the model. The model now has the tool's result and can decide what to do with it — often it replies to the user with a summary of what happened, but it might call another tool first.

Step 3 is the thing that makes this an actual **loop**. In step 00, `chat()` was one LLM call per user message. In step 01, `chat()` is *potentially many* LLM calls per user message, one per round of tool use, until the model stops asking for tools and emits its final text reply.

## Key decisions

### Decision 1: JSON Schema for tool parameters

Every tool declares a JSON Schema describing its arguments. For `read`:

```python
{
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path to the file to read"},
    },
    "required": ["path"],
}
```

The LLM uses this schema to validate its own argument structure before emitting a tool call. Good schemas mean the model gets the arguments right most of the time. Bad schemas mean it makes things up.

Two non-obvious things JSON Schema is doing for us:
- **Required fields** force the model to include them. Omitting `"required": ["path"]` and the model will happily call `read()` with no path.
- **Descriptions matter.** The model reads them as part of its prompt. A description like "path" is worse than "Path to the file to read, relative or absolute." The more concrete your descriptions, the fewer hallucinated arguments.

### Decision 2: tools know nothing about the session

Notice that the tool functions in `builtin_tools.py` take a `session` parameter — `session: "AgentSession"` — but most of them don't use it. Why pass it at all?

Because a tool might need it later. A tool that reads the user's config, or logs to the session, or queries the session's tool registry, needs access. Keeping `session` in the tool signature makes the interface uniform: every tool, current or future, can reach into the session if it needs to. Tools that don't care just ignore it.

### Decision 3: a while-loop inside `chat()`

Look at step 01's `AgentSession.chat()`:

```python
while True:
    messages = self.state.build_messages()
    content, tool_calls = await self.agent.llm.chat(messages, tool_schemas)
    # ... append assistant message to history ...
    if not tool_calls:
        break
    await self._handle_tool_calls(tool_calls)
```

Compare step 00's one-shot version:

```python
response = await self.agent.llm.chat(messages)
```

Step 01's `chat()` can run through the LLM multiple times for a single user message. As long as the model keeps asking for tools, we keep executing them and feeding results back. Only when the model emits text without tool calls does the loop exit.

This has a subtle safety consequence: in principle, the model could get stuck in a loop, calling tools forever. We don't protect against that here — partly because ChatGPT usually converges, partly because a real framework would cap iterations. Adding a max-iteration guard is a good 10-line exercise.

### Decision 4: `asyncio.gather` for parallel tool execution

When the model asks for multiple tools in one turn, we run them concurrently:

```python
tool_call_results = await asyncio.gather(
    *[self._execute_tool_call(tool_call) for tool_call in tool_calls]
)
```

If the model asks to `read` three files, all three reads happen at once, not serially. For I/O-heavy tools this matters a lot. For a `bash` tool, it means three `bash` calls can run in parallel too — which might not always be safe (imagine two `bash` calls both doing `cd`). Worth flagging, not worth fixing yet.

## Read the code

### 1. `src/mybot/tools/base.py` — the tool interface

Two pieces: `BaseTool` (abstract base) and `@tool` (decorator that wraps a function as a tool).

```python
class BaseTool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

    @abstractmethod
    async def execute(self, session: "AgentSession", **kwargs: Any) -> str:
        ...

    def get_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
```

Every tool is a subclass (or a `FunctionTool` produced by the decorator) with `name`, `description`, `parameters`, and an async `execute` method that returns a string. The tool's output is always a string — that's what the model sees next turn. If your tool returns something complex (a dict, an image), serialize it first.

The `get_tool_schema()` method emits the Chat-Completions-shape schema. `LLMProvider._translate_tools()` (from step 00) flattens it into Responses API shape on the way out.

### 2. `src/mybot/tools/builtin_tools.py` — four tools, ~100 lines total

Each one is a function with a `@tool(...)` decorator on top:

```python
@tool(
    name="read",
    description="Read the contents of a text file",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read"},
        },
        "required": ["path"],
    },
)
async def read_file(path: str, session: "AgentSession") -> str:
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except PermissionError:
        return f"Error: Permission denied reading: {path}"
    ...
```

Notice all the error handling returns **strings**, not exceptions. Why? Because the next turn will show this string to the LLM. An exception would crash the agent; a string tells the model "that didn't work, try something else." Error-as-data is a design choice worth copying in your own tools.

### 3. `src/mybot/tools/registry.py` — hold them all

```python
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [tool.get_tool_schema() for tool in self._tools.values()]

    async def execute_tool(self, name: str, session, **kwargs) -> str:
        tool = self.get(name)
        if tool is None:
            raise ValueError(f"Tool not found: {name}")
        return await tool.execute(session=session, **kwargs)

    @classmethod
    def with_builtins(cls) -> "ToolRegistry":
        registry = cls()
        registry.register(read_file)
        registry.register(write_file)
        registry.register(edit_file)
        registry.register(bash)
        return registry
```

Plain dict of name → `BaseTool`. `get_tool_schemas()` produces the list to send to the model. `execute_tool()` runs one by name. `with_builtins()` is a convenience for "give me a registry with the four defaults already registered."

Step 02 uses the same registry pattern for skills. Step 06 adds web tools to it. Step 15 adds agent-dispatch as a tool. The pattern is reusable.

### 4. `src/mybot/core/agent.py` — the loop body

The important bit:

```python
async def chat(self, message: str) -> str:
    user_msg: Message = {"role": "user", "content": message}
    self.state.add_message(user_msg)

    tool_schemas = self.tools.get_tool_schemas()

    while True:
        messages = self.state.build_messages()
        content, tool_calls = await self.agent.llm.chat(messages, tool_schemas)

        # Build the assistant's message. If it made tool calls, nest them
        # in the Chat-Completions `tool_calls` field.
        assistant_msg: Message = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in tool_calls
            ]
        self.state.add_message(assistant_msg)

        if not tool_calls:
            break

        await self._handle_tool_calls(tool_calls)
```

Walk through it once with an example. User types "what's in /etc/hosts":

1. User message goes into history: `[{"role": "user", "content": "what's in /etc/hosts"}]`.
2. Enter loop. Build full history with system prompt: `[{"role":"system",...}, user_msg]`.
3. Send to LLM with tool schemas. Model decides it needs `read`. Returns `content=""`, `tool_calls=[LLMToolCall(name="read", args='{"path":"/etc/hosts"}')]`.
4. Append the assistant message (with `tool_calls` field) to history.
5. `tool_calls` is non-empty, so call `_handle_tool_calls`. That runs `read("/etc/hosts")`, gets back the file contents, appends `{"role": "tool", "content": "<file contents>", "tool_call_id": "..."}` to history.
6. Loop continues. Rebuild history, send again.
7. Model now has the file contents. Returns `content="The /etc/hosts file contains..."`, `tool_calls=[]`.
8. Append the assistant message (no `tool_calls` field). `tool_calls` empty → break.
9. Return `content`.

That's the full flow. The model might chain multiple tool calls — e.g. first `read` to look at a file, then `edit` to modify it — and the while-loop handles each round transparently.

### 5. `src/mybot/provider/llm/base.py` — the LLM wrapper, tool-aware

The step 01 `LLMProvider.chat()` returns `tuple[str, list[LLMToolCall]]` instead of just `str`. That's the only shape change from step 00. Internally, the same `_translate_messages` / `_translate_tools` / `aggregate_stream` plumbing handles the new `function_call` path.

`LLMToolCall` is a simple dataclass with `id`, `name`, and `arguments` (the raw JSON string). The session layer parses the arguments via `json.loads` in `_execute_tool_call`.

## Try it out

```bash
uv run my-bot chat
```

Try:

```
You: what's in /etc/hosts?
pickle: [reads the file, summarizes it]

You: create a file at /tmp/hello.txt that says "hello from pickle"
pickle: [writes the file, confirms]

You: now read it back
pickle: [reads, shows you the contents]

You: run the command `date`
pickle: [runs it, shows the output]
```

A conversation that takes action.

## Exercises

1. **Add your own tool.** Open `builtin_tools.py` and add an `@tool`-decorated function — say, a `word_count` tool that takes a string and returns its word count. Register it in `registry.py`'s `with_builtins()`. Rerun chat; ask the agent to "count the words in the sentence 'quick brown fox'."

2. **Watch a tool call happen.** Add `print(tool_calls)` right after `content, tool_calls = await ...` in `agent.py`. Send "read /etc/hosts". See the raw `LLMToolCall` that comes back. Notice the `arguments` is a JSON **string**, not a dict — that's the wire format.

3. **Break a tool.** Replace `read_file`'s body with `raise RuntimeError("tool crashed")`. Rerun chat; ask to read a file. Watch the agent handle the exception — the exception-as-string pattern means the model sees "Error executing tool: tool crashed" and typically apologizes instead of crashing. Put the body back.

4. **Ask for something malicious.** Try `"delete every file in /tmp"`. The model will use `bash` to do it (yes, really — this step has no safety guardrails). This is the point where you realize agents need sandboxing. Step 16 of the tutorial touches concurrency; real-world sandboxing is out of scope.

## What breaks next

Tools are ad-hoc — they live in your Python code. What if you want a library of reusable agent behaviors, shareable across agents, editable without touching Python?

That's what step 02 (`02-skills`) adds — `SKILL.md` files that the agent can load on demand.

## What's Next

[Step 02: Skills](../02-skills/) — extend your agent without editing Python.
