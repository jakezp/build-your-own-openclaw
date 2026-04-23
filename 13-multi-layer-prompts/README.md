# Step 13: Multi-Layer Prompts

> More context, more context, more context.

## Prerequisites

- Steps 00–12 done.

```bash
cd 13-multi-layer-prompts
uv sync
```

## Why this step exists

Since step 00, the system prompt has been one block — the body of `AGENT.md`. A persona, nothing more.

That was fine when the agent was just "a persona talking to one user in one place." As of step 12, the agent is:

- Running across multiple channels (CLI, Telegram, Discord, WebSocket).
- Handling scheduled cron triggers that look nothing like user messages.
- Aware of other agents it can dispatch to.
- Backed by subagents (step 15) and memory (step 17).

All of that should influence how the model behaves. A cron trigger shouldn't get a chatty reply aimed at a human. A Telegram message should respect Telegram's markdown quirks. A subagent dispatch needs to know it's a subagent.

A single flat `AGENT.md` can't carry all of that. We need to **compose** the system prompt at runtime from layers.

## The mental model

Every LLM turn, before we send the request, we build the system prompt fresh. Five layers, joined with blank lines:

1. **Identity** — the agent's `AGENT.md` body. "You are Pickle, a cat assistant."
2. **Personality** (optional) — the agent's `SOUL.md`, if present. Tone, style, quirks.
3. **Bootstrap context** — workspace-wide documents (`BOOTSTRAP.md`, `AGENTS.md`) plus a dynamic list of scheduled tasks.
4. **Runtime info** — which agent is running right now, current timestamp.
5. **Channel hint** — how this response will be delivered (cron? subagent? Telegram? CLI?).

The composition happens in `PromptBuilder.build(state)`. It returns one big string that becomes the system prompt.

Layers 1 and 2 come from the agent itself. Layers 3, 4, 5 come from the workspace and the runtime state. Every layer is optional-ish — if there's no `SOUL.md`, skip layer 2. If `BOOTSTRAP.md` doesn't exist, skip that part of layer 3.

## Key decisions

### Decision 1: build fresh every turn

We don't cache the system prompt. Every call to `state.build_messages()` rebuilds it.

Why? Because layers 3, 4, 5 change:

- Runtime timestamp ticks forward.
- Bootstrap context picks up new crons as you add CRON.md files.
- Channel hint depends on the event source.

Caching would need invalidation, which means bookkeeping. Rebuilding is ~1ms. Trivial next to the LLM call. Not a real cost.

### Decision 2: markdown headings, not JSON

Each layer is markdown-formatted with `## Section Name` headings:

```
You are Pickle, a cat assistant.
...

## Personality

Friendly, playful, signs off with emoji.

## Scheduled Tasks

- Daily News: 7am news roundup
- Weekly Report: Friday afternoon summary

## Runtime

Agent: pickle
Time: 2026-04-23T14:30:00

## Channel

You are responding via telegram.
```

JSON would be more structured but the model prefers markdown. Models have read more markdown than JSON in training, so they pattern-match sections better when formatted as documents.

### Decision 3: `SOUL.md` is optional and separate from `AGENT.md`

You could cram personality into `AGENT.md`. Most tutorials do. We split it:

- `AGENT.md` body is "what the agent does." Persona, capabilities, rules.
- `SOUL.md` body is "how the agent feels." Tone, voice, mood.

Two reasons:

1. **Reuse.** A single `SOUL.md` can be shared across multiple `AGENT.md`s. Your "friendly tone" isn't specific to one agent.
2. **Editability.** Sometimes you want to tweak tone without touching rules. Having them in separate files makes the git diffs cleaner.

`AgentLoader` already loads `SOUL.md` if it exists next to `AGENT.md`. `PromptBuilder` injects it as layer 2.

### Decision 4: channel hint is authoritative for "how to respond"

The channel hint at the bottom is a short statement:

- `"You are responding via telegram."`
- `"You are running as a background cron job. Your response will not be sent to user directly."`
- `"You are running as a dispatched subagent. Your response will be sent to main agent."`

Why this matters: the model reads the whole prompt top-to-bottom. The last thing it sees before the conversation starts is "here's who's reading your reply." That primes the response shape.

A cron-triggered agent answers differently than a chat-triggered agent. The channel hint makes that explicit.

### Decision 5: bootstrap context is optional and user-owned

`BOOTSTRAP.md` and `AGENTS.md` at the workspace root are YOUR docs. They say things like:

- "The user's name is Zane."
- "Always write in American English."
- "Agents available: pickle (chat), cookie (memory), scout (web)."

These are global to all agents running in this workspace. If they're missing, the layer is empty. No error.

This is where "teach the agent about your environment" happens. You write these files; the prompt builder includes them.

## Read the code

### 1. `src/mybot/core/prompt_builder.py` — the composer

```python
class PromptBuilder:
    def __init__(self, context: "SharedContext"):
        self.context = context

    def build(self, state: "SessionState") -> str:
        layers = []

        # Layer 1: Identity (the AGENT.md body)
        layers.append(state.agent.agent_def.agent_md)

        # Layer 2: Soul (optional)
        if state.agent.agent_def.soul_md:
            layers.append(f"## Personality\n\n{state.agent.agent_def.soul_md}")

        # Layer 3: Bootstrap context
        bootstrap = self._load_bootstrap_context()
        if bootstrap:
            layers.append(bootstrap)

        # Layer 4: Runtime context
        layers.append(self._build_runtime_context(
            state.agent.agent_def.id, datetime.now()))

        # Layer 5: Channel hint
        layers.append(self._build_channel_hint(state.source))

        return "\n\n".join(layers)
```

Five layer methods, one `join`. The shape is intentionally boring — the complexity is in the content of each layer, not the composition logic.

### 2. `_load_bootstrap_context` — concatenate static + dynamic

```python
def _load_bootstrap_context(self) -> str:
    parts = []

    bootstrap_path = self.context.config.workspace / "BOOTSTRAP.md"
    if bootstrap_path.exists():
        parts.append(bootstrap_path.read_text().strip())

    agents_path = self.context.config.workspace / "AGENTS.md"
    if agents_path.exists():
        parts.append(agents_path.read_text().strip())

    cron_list = self._format_cron_list()
    if cron_list:
        parts.append(cron_list)

    return "\n\n".join(parts)
```

Read `BOOTSTRAP.md`, read `AGENTS.md`, then synthesize a list of scheduled cron jobs via `_format_cron_list`. The cron list updates every turn — add a new cron, the agent sees it on the next message.

### 3. `_build_channel_hint` — source-aware message

```python
def _build_channel_hint(self, source: "EventSource") -> str:
    if source.is_cron:
        return "You are running as a background cron job. Your response will not be sent to user directly."
    if source.is_agent:
        return "You are running as a dispatched subagent. Your response will be sent to main agent."
    elif source.is_platform:
        return f"You are responding via {source.platform_name}."
    else:
        raise ValueError(f"Unknown source type: {source}")
```

`EventSource` subclasses carry `is_cron`, `is_agent`, `is_platform` flags. The prompt builder branches on the flags to produce the right hint. A subclass we haven't added yet would fail this branch — deliberate, so adding a new source type forces you to update the hint.

### 4. `SessionState` picks up the builder

`SessionState.build_messages()` now delegates to `PromptBuilder`:

```python
def build_messages(self) -> list[Message]:
    system_prompt = self.prompt_builder.build(self)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(self.messages)
    return messages
```

Same shape as step 00's `build_messages()` except the system prompt is composed, not a single agent.md body.

## Try it out

Add a `BOOTSTRAP.md` to your workspace:

```bash
cat > ../default_workspace/BOOTSTRAP.md <<'EOF'
## About the user

The user's name is Zane. They're building an agent tutorial.
Always use American English spelling.
EOF
```

Run chat. The model will refer to you as Zane without you introducing yourself. Why? Because layer 3 injected the bootstrap into the system prompt.

Try swapping personality:

```bash
cat > ../default_workspace/agents/pickle/SOUL.md <<'EOF'
Respond like a sleepy cat that just woke up. Short sentences. Yawns.
EOF
```

Now the same agent feels different — same rules (AGENT.md), different tone (SOUL.md).

## Exercises

1. **Inspect the built prompt.** Add `print(system_prompt)` inside `build_messages()`. Send a message. See the full composed prompt, all five layers.

2. **Remove a layer.** Rename `BOOTSTRAP.md` to `_BOOTSTRAP.md`. The model no longer knows your name. Put the file back.

3. **Add a layer.** Write a new method `_build_memory_summary(state)` that queries your memory files (which you'll set up in step 17) and injects a "known facts about the user" paragraph. Wire it into `build()`.

4. **Break the channel hint.** Introduce a new `EventSource` subclass without setting any `is_*` flag. Send an event with that source. Watch `_build_channel_hint` raise. This is the design forcing you to decide what to say for new source types.

## What breaks next

The agent knows about its environment now. But it still only replies when a human talks first. Even if a cron fires, the agent's reply has nowhere to go — no human is watching the terminal.

Step 14 adds `post_message`: a tool the agent calls to proactively send an OutboundEvent. Cron fires, agent does work, agent calls `post_message` to land its findings in the right Telegram chat. The user wakes up and sees a morning brief that wasn't triggered by typing anything.

## What's Next

[Step 14: Post Message Back](../14-post-message-back/) — your agent speaks first.
