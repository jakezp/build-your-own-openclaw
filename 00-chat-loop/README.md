# Step 00: A Chat Loop

> Your first agent. It does exactly one thing: talk back.

## Prerequisites

**Finish the OAuth walkthrough first:** [`../000-oauth/`](../000-oauth/). You need a valid Token_Store on disk before `my-bot chat` can do anything.

Once `000-oauth/` is green, copy the example config for this step:

```bash
cp ../default_workspace/config.example.yaml ../default_workspace/config.user.yaml
```

The file pre-selects `model: gpt-5.4` and `default_agent: pickle`. You can change either. Don't add credentials — those live in the Token_Store.

Install this step's dependencies:

```bash
cd 00-chat-loop
uv sync
```

## Why this step exists

You've proved you can log in and send a raw request to the Responses API. Congratulations — you now have the most boring chat client imaginable. It can send ONE message, show ONE reply, and forget everything.

That's not a chat. That's a shout into the void.

A chat has **continuity**. Each reply is informed by everything that came before. If you ask "my name is Zane," and then ask "what's my name?", you expect it to remember. The Responses API has no memory on its own — it answers exactly the prompt you send, nothing more. Continuity has to be manufactured by the *caller*, which means your code.

That's what we're building. A loop that reads your input, appends it to a running history, sends the whole history to the model, appends the reply to the history, prints the reply. Repeat.

Everything else in this tutorial — tools, persistence, skills, channels, memory — is layers on top of this loop.

## The mental model

The smallest viable agent has three moving parts:

1. **A conversation state** — a list of messages you're accumulating. One message per turn, labeled `"user"` or `"assistant"`.
2. **A thing that sends messages to the model** — takes your message list, returns the next assistant message.
3. **A loop** — reads user input, calls (2), adds the result to (1), prints it.

That's it. The rest is naming.

In the code you'll see three classes that map to those three pieces:

| Concept | Class | File |
|---|---|---|
| Conversation state | `SessionState` | `src/mybot/core/session_state.py` |
| Thing that sends messages | `LLMProvider` | `src/mybot/provider/llm/base.py` |
| Loop (and orchestration) | `ChatLoop` + `AgentSession` | `src/mybot/cli/chat.py`, `src/mybot/core/agent.py` |

A fourth concept, `Agent`, holds the first three together and sets a **persona** — a system prompt that tells the model who it's pretending to be ("You are Pickle, a cat assistant"). Swapping agents means swapping personas. We'll keep it simple here; later steps add real behaviors.

## Key decisions

Three things were chosen deliberately and worth naming.

### Decision 1: full-history send, every turn

Every time the user types something, we send the **entire history** back to the model — the system prompt, every past user message, every past assistant reply — plus the new user message. The model then generates a reply that's aware of all of it.

Why: the Responses API is stateless. The server doesn't remember anything between requests. "The model remembers" is an illusion the caller maintains by re-sending history.

Cost: we send more tokens per turn as the conversation grows. For a casual chat this is fine. For a long agent session, it becomes a problem. Step 05 (`05-compaction`) is where we start solving it.

### Decision 2: Chat-Completions shape internally

The Responses API wants a top-level `instructions` string plus an `input` list of role/content items. But the rest of the agent-building world — every tutorial, every library, every API — uses Chat-Completions shape: one flat `messages` list where the first message is `{"role": "system", ...}`.

We picked Chat-Completions as our internal shape because it's what anyone reading this code has seen before. The tutorial's internal "message" looks like this:

```python
{"role": "user", "content": "hello"}
```

We translate to the Responses API shape at the very last moment, inside `LLMProvider.chat()`. Specifically, `_translate_messages` pulls the system message out into `instructions` and puts everything else into `input`. This is the only place in the code that knows about the Responses API wire format. Every other file can stay format-agnostic.

### Decision 3: agents are markdown files

An "agent" in this tutorial is a directory under `default_workspace/agents/` with an `AGENT.md` file inside. The file has YAML frontmatter (name, description) and a markdown body — the body becomes the system prompt.

Look at `default_workspace/agents/pickle/AGENT.md` to see one. The whole thing is:

```
---
name: Pickle
description: A friendly cat assistant
---

You are Pickle, a cat assistant who loves to help. You are friendly,
playful, and always sign off with a cat-related emoji.
```

Why markdown with frontmatter? Because it's human-editable, human-readable, and stays out of Python source. Step 02 extends this pattern to "skills" — reusable chunks of prompt-shaped behavior.

## Read the code

The whole step is small. Skim these four files in order — each builds on the previous.

### 1. `src/mybot/core/session_state.py` — the container

```python
@dataclass
class SessionState:
    """Pure conversation state container."""

    session_id: str
    agent: "Agent"
    messages: list[Message]

    def add_message(self, message: Message) -> None:
        self.messages.append(message)

    def build_messages(self) -> list[Message]:
        system_prompt = self.agent.agent_def.agent_md
        messages: list[Message] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.messages)
        return messages
```

- Dataclass. No logic. Stores a list of messages, remembers which agent owns the session.
- `add_message` appends. `build_messages` returns a list ready to send to the LLM — always prepends the system prompt (pulled from the agent's `AGENT.md` body), then all the history.
- This is "pure" in the sense that swapping `SessionState` for a subclass with different behavior (a `PersistentSessionState` that writes to disk, maybe) wouldn't change the rest of the step. The pattern will come back in step 03.

### 2. `src/mybot/provider/llm/base.py` — the LLM wrapper

```python
class LLMProvider:
    def __init__(self, model: str, temperature: float = 0.7, ...):
        self.model = model
        self._oauth = ChatGPTOAuth()      # handles the Token_Store
        self._client = ResponsesClient()  # handles SSE

    async def _resolve_credential(self) -> tuple[str, str]:
        token = await self._oauth.access_token()
        account_id = await self._oauth.account_id()
        return token, account_id

    async def chat(self, messages: list[dict], **kwargs) -> str:
        access_token, account_id = await self._resolve_credential()
        instructions, input_items = _translate_messages(messages)
        request = ResponsesRequest(
            model=self.model,
            instructions=instructions,
            input=input_items,
        )
        events = self._client.stream(
            request, access_token=access_token, account_id=account_id
        )
        aggregated = await aggregate_stream(events)
        return aggregated.content
```

- Holds a `ChatGPTOAuth` and a `ResponsesClient` (both from the `000-oauth/` walkthrough).
- `_resolve_credential` acquires the access token (refreshing if needed) AND the account id. Returns them as a tuple.
- `chat` is the one interesting method. Six lines of logic:
  1. Get a valid (token, account_id).
  2. Translate our Chat-Completions-shape history into (instructions, input).
  3. Build a `ResponsesRequest`.
  4. Start the SSE stream.
  5. Aggregate deltas into a final string.
  6. Return it.
- Return type is just `str`. No tool calls, no structured output. Step 01 adds tool calls and changes this to `tuple[str, list[LLMToolCall]]`.

### 3. `src/mybot/core/agent.py` — Agent + AgentSession

```python
class Agent:
    """A configured agent that creates and manages conversation sessions."""

    def __init__(self, agent_def: "AgentDef", config: "Config") -> None:
        self.agent_def = agent_def
        self.config = config
        self.llm = LLMProvider.from_config(agent_def.llm)

    def new_session(self, session_id: str | None = None) -> "AgentSession":
        state = SessionState(
            session_id=session_id or str(uuid.uuid4()),
            agent=self,
            messages=[],
        )
        return AgentSession(agent=self, state=state)


@dataclass
class AgentSession:
    agent: Agent
    state: SessionState
    started_at: datetime = field(default_factory=datetime.now)

    async def chat(self, message: str) -> str:
        user_msg: Message = {"role": "user", "content": message}
        self.state.add_message(user_msg)

        messages = self.state.build_messages()
        response = await self.agent.llm.chat(messages)

        assistant_msg: Message = {"role": "assistant", "content": response}
        self.state.add_message(assistant_msg)

        return response
```

This is where the loop body lives — `AgentSession.chat()`. Five lines of actual work:

1. Wrap the user's string as a `{"role": "user", ...}` message.
2. Append to the session state.
3. Build the full history (system + all prior turns) via `state.build_messages()`.
4. Send it to the LLM.
5. Wrap the reply as `{"role": "assistant", ...}`, append, return.

The split between `Agent` and `AgentSession` matters: one `Agent` can have many sessions (one per conversation), but all of them share the same `LLMProvider`, `agent_def`, and `config`. The `Agent` is heavy and long-lived; the `AgentSession` is light and per-conversation.

### 4. `src/mybot/cli/chat.py` — the user-facing loop

```python
async def run(self) -> None:
    self.console.print(Panel(Text("Welcome to my-bot!", style="bold cyan"), ...))
    self.console.print("Type 'quit' or 'exit' to end the session.\n")

    try:
        while True:
            user_input = await asyncio.to_thread(self.get_user_input)

            if user_input.lower() in ("quit", "exit", "q"):
                break
            if not user_input:
                continue

            try:
                response = await self.session.chat(user_input)
                self.display_agent_response(response)
            except Exception as e:
                self.console.print(f"\n[bold red]Error:[/bold red] {e}\n")

    except (KeyboardInterrupt, EOFError):
        pass
```

The human-loop part. Notice:

- `asyncio.to_thread(self.get_user_input)` — `Prompt.ask` is synchronous and would block the event loop. We park it in a thread so the rest of the async world keeps running. If you removed this, typing a reply would freeze everything.
- Errors from `session.chat()` are caught and displayed in red. A broken turn doesn't kill the loop.
- Ctrl+C / Ctrl+D exit cleanly.

## Try it out

With the config copied and `my-bot login` completed in step 000:

```bash
cd 00-chat-loop
uv run my-bot chat
```

You should see a welcome panel, then a `You:` prompt. Try:

```
You: My name is Zane.
pickle: Nice to meet you, Zane! ...
You: What's my name?
pickle: Your name is Zane! ...
```

If the second answer contains your name, **continuity is working** — the session is re-sending the history every turn.

## Exercises

1. **Swap the persona.** Open `default_workspace/agents/pickle/AGENT.md` and rewrite the body. Maybe make it a cranky librarian or a medieval knight. Rerun `my-bot chat` and confirm the personality changed.

2. **Add a second agent.** Copy the `pickle/` directory to `librarian/`, edit its `AGENT.md` name and body, then run `uv run my-bot chat --agent librarian`. Two agents, same Token_Store, same loop.

3. **See the full history.** Add `print(messages)` just before `response = await self.agent.llm.chat(messages)` in `agent.py`. Send a few turns. Watch the history grow. This is exactly what the model sees every turn.

4. **Break continuity.** In `session_state.py`, comment out `messages.extend(self.messages)` in `build_messages()`. Now every turn sends only the system prompt — no history. Ask "my name is Zane" then "what's my name?". Watch the model forget. Put the line back.

## What breaks next

You have a chat loop. What can't it do?

- **Take action in the world.** It can only talk. It can't read a file, run a command, search the web. Step 01 fixes this with *tools*.
- **Remember across runs.** Quit the CLI, re-run it, and the conversation starts fresh. Step 03 adds persistence.
- **Hold long conversations.** The full-history send gets expensive as turns pile up. Step 05 adds compaction.
- **Do more than one thing per session.** The system prompt is static. Step 02 adds skills — reusable modular prompts.

Each next step solves exactly one pain point from that list.

## What's Next

[Step 01: Tools](../01-tools/) — let your agent do things, not just talk.
