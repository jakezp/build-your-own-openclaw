# Step 04: Slash Commands

> Direct user control over the session. No LLM involved.

## Prerequisites

- [`000-oauth/`](../000-oauth/), [`00-chat-loop/`](../00-chat-loop/), [`01-tools/`](../01-tools/), [`02-skills/`](../02-skills/), [`03-persistence/`](../03-persistence/) done.

```bash
cd 04-slash-commands
uv sync
```

## Why this step exists

Every interaction in steps 00–03 goes through the model. Want to list your past sessions? You have to ask the model, it calls a tool, you get back the list. That works, but it's slow (LLM round-trip) and unreliable (model might summarize instead of listing verbatim).

Sometimes you just want to **tell** the agent something. Not have a conversation about it. You want:

- `/help` → print a cheat sheet immediately.
- `/skills` → show me the skill catalog.
- `/session` → tell me the session id, message count, creation time.

These aren't LLM tasks — they're local, deterministic, instant. A slash command skips the model entirely and runs a Python function.

This step introduces a `CommandRegistry` that lives beside the `ToolRegistry`. When the user's input starts with `/`, it goes to the command registry. Otherwise, it goes through the normal chat flow.

## The mental model

A slash command is a function that:

1. Takes the rest of the user's input as a string (anything after the command name).
2. Takes the current `AgentSession` so it can read state.
3. Returns a string that gets printed to the terminal.

No LLM call. No network I/O. No tool-call round-trip. Just Python.

The CLI chat loop now has two code paths:

```python
if user_input.startswith("/"):
    response = await command_registry.dispatch(user_input, session)
else:
    response = await session.chat(user_input)
```

A slash command handles user-facing concerns (display, debug, control). A tool handles model-facing concerns (external actions the model wants to take). They're parallel systems serving different masters.

## Key decisions

### Decision 1: commands are orthogonal to tools

A command like `/skills` has the same job as a tool like `skill` — both let you inspect the skill catalog. Why both?

- **Tools are for the model.** The model reads a schema, decides to invoke one. The user sees the model's narrated reply.
- **Commands are for the user.** The user types `/skills`, sees the raw output, no model involvement.

Both can coexist. Sometimes you want the model to reach for a skill as part of its reasoning (`skill` tool). Sometimes you want to browse skills yourself (`/skills` command). Different access patterns to the same underlying state.

### Decision 2: three commands, room for more

Step 04 ships with `/help`, `/skills`, `/session`. That's intentionally small — it establishes the pattern; later steps add more. Step 05 adds `/compact`. Step 07 adds `/resume`. By step 17 you've got maybe a dozen.

Keep the first pass minimal. Resist the urge to frontload.

### Decision 3: `list_commands` deduplicates by name, not key

Look at `list_commands()`:

```python
def list_commands(self) -> list[Command]:
    seen = set()
    commands = []
    for cmd in self._commands.values():
        if cmd.name not in seen:
            seen.add(cmd.name)
            commands.append(cmd)
    return commands
```

Why this loop shape? Because aliases register the same `Command` under multiple keys. `HelpCommand` registers itself as both `help` and `?`, so `_commands` has two entries pointing to the same instance. When `/help` wants to show "available commands," we want to see `help` listed once (with `?` as an alias), not twice.

Deduplication by `cmd.name` (the canonical name) gets that right.

## Read the code

### 1. `src/mybot/core/commands/base.py` — the interface

```python
class Command(ABC):
    name: str
    aliases: list[str] = []
    description: str = ""

    @abstractmethod
    async def execute(self, args: str, session: "AgentSession") -> str:
        ...
```

A command has four things: a name, zero or more aliases, a description (for `/help` output), and an `execute` method.

`execute` takes `args` (whatever the user typed after the command name, as a single raw string) and the `AgentSession`. It's async because later commands (step 05's `/compact`) call the LLM internally. `/session` and `/skills` don't need the LLM, but they still have to match the signature.

### 2. `src/mybot/core/commands/registry.py` — the dispatcher

The dispatcher has three jobs:

```python
def resolve(self, input: str) -> tuple[Command, str] | None:
    if not input.startswith("/"):
        return None
    parts = input[1:].split(None, 1)
    if not parts:
        return None
    cmd_name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    cmd = self._commands.get(cmd_name)
    if cmd:
        return (cmd, args)
    return None
```

1. **Is this input a command?** If it doesn't start with `/`, return `None` — the chat loop should do its normal thing.
2. **Parse it.** Everything up to the first whitespace is the command name; everything after is the args string.
3. **Look it up.** Return the command + args, or `None` if no command matches.

The `dispatch()` method runs it:

```python
async def dispatch(self, input: str, session) -> str | None:
    resolved = self.resolve(input)
    if not resolved:
        return None
    cmd, args = resolved
    return await cmd.execute(args, session)
```

Split from `resolve()` because step 07's `/resume` command wants to *parse* the input in one place and *execute* it somewhere else (after a session switch).

### 3. `src/mybot/core/commands/handlers.py` — the three builtins

Each one is a small subclass with a single `execute` method. The interesting one is `SkillsCommand`, which has TWO modes:

```python
class SkillsCommand(Command):
    name = "skills"

    async def execute(self, args: str, session) -> str:
        if not args:
            # Just list them.
            skills = session.agent.skill_loader.discover_skills()
            ...
            return list_output

        # Otherwise, args is a skill ID; show its details.
        skill_id = args.strip()
        try:
            skill = session.agent.skill_loader.load_skill(skill_id)
        except DefNotFoundError:
            return f"✗ Skill `{skill_id}` not found."
        ...
        return details_output
```

`/skills` lists; `/skills haiku` shows the haiku skill's details. The "pass a single positional arg" pattern is useful — step 07's `/resume <session-id>` uses the same shape.

### 4. `src/mybot/cli/chat.py` — the two-path dispatch

```python
while True:
    user_input = await asyncio.to_thread(self.get_user_input)
    ...

    # Check for slash commands first.
    cmd_response = await self.session.command_registry.dispatch(
        user_input, self.session
    )
    if cmd_response is not None:
        self.console.print(cmd_response)
        continue

    # Otherwise, normal chat.
    response = await self.session.chat(user_input)
    self.display_agent_response(response)
```

The `is not None` check matters: a command that returns an empty string is still a command (just one that chose to say nothing). `None` specifically means "this wasn't a command, fall through to chat."

## Try it out

```bash
uv run my-bot chat
```

Try:

```
You: /help
Available Commands:
/help, /? - Show available commands
/skills - List all skills or show skill details
/session - Show current session details

You: /session
Session ID: 4e8f...
Agent: Pickle (`pickle`)
Created: 2026-04-23T...
Messages: 0

You: /skills haiku
Skill: `haiku`
Name: Haiku
Description: Respond in 5-7-5 haiku form...
---
SKILL.md:
[full body]

You: hi pickle
pickle: [normal chat, no slash command involved]
```

## Exercises

1. **Add a `/clear` command.** It empties `session.state.messages` (but leaves the system prompt intact). Register it in `with_builtins()`. Test that after `/clear`, the model forgets everything — but in-memory only. Check the on-disk history: it's still there. That's Decision 2 from step 03 coming back — persistence is a separate concern.

2. **Add an alias to `/session`.** Maybe `/info` or `/whoami`. Two lines of code.

3. **Make a command that takes multiple args.** Hint: `args.split()`. Consider a `/rename <new_name>` that edits `session.agent.agent_def.name` for the rest of the session (in-memory only).

4. **Find the bug.** `SkillsCommand` references `session.agent.skill_loader`. If an agent's `AGENT.md` has `allow_skills: false`, is `skill_loader` still available? Read `Agent.__init__` — then decide if this is a real bug and how you'd fix it.

## What breaks next

You have deterministic control now. What can't you do?

Long conversations are still expensive — you're sending the full history every turn. By turn 50 you're paying for a lot of re-sending. Step 05 adds **context compaction**: when history gets too big, summarize old turns and replace them with a shorter version.

## What's Next

[Step 05: Compaction](../05-compaction/) — squeeze long histories down.
