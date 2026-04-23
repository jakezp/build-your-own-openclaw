# Step 02: Skills

> Reusable prompt-shaped behavior your agent can load on demand.

## Prerequisites

- [`000-oauth/`](../000-oauth/), [`00-chat-loop/`](../00-chat-loop/), [`01-tools/`](../01-tools/) done.

```bash
cd 02-skills
uv sync
```

## Why this step exists

Step 01 showed you tools — Python functions the model can call. Tools are great for anything that touches the outside world (filesystem, network, shell). But they're a poor fit for something much simpler: **instructions**.

Say you want an agent that can, when needed, write code review feedback following your team's style guide. You *could* build a Python tool that holds the style guide in a string and returns it. That's fine, but now your style guide lives in Python. Every edit requires a code change. And anyone who wants to reuse this behavior has to copy-paste Python.

What if instead the style guide were just a markdown file? The agent, mid-conversation, decides "I should load the code review guide now." Your framework fetches the file, its contents get injected into the conversation, the model now has the instructions in context and can follow them. Edit the markdown, the agent's behavior changes — no Python touched.

That's a **skill**. In this step we add skills as a first-class concept. Think of them as "prompt modules" — bundles of instructions the model can pull in as needed.

## The mental model

A skill is:

1. **A directory** under `default_workspace/skills/<skill-id>/` with a `SKILL.md` file inside.
2. **The file's frontmatter** declares `name` and `description`.
3. **The file's body** is the actual instructions — whatever you want the model to know when this skill is active.

For example, `default_workspace/skills/code-review/SKILL.md` might be:

```
---
name: Code Review
description: Review code for style, correctness, and security. Load this when the user asks for a code review or asks you to critique code.
---

When reviewing code, consider:

- **Naming**: is the name honest? Does it hide complexity, or surface it?
- **Structure**: is the code as flat as possible? ...
- **Safety**: are there error paths? ...
```

The important mechanism: the agent discovers all skills at startup and registers ONE tool called `skill`. The `skill` tool's description **embeds the list of available skills**, so when the model reads its tool schema, it sees:

```
Load and invoke a specialized skill.
<skills>
  <skill name="Code Review">Review code for style, correctness, and security. Load this when the user asks for a code review...</skill>
  <skill name="Bug Triage">Systematically narrow down a bug report. Load when debugging.</skill>
</skills>
```

The model picks a skill by name, calls `skill(skill_name="code-review")`, and the tool's return value is the full markdown body. That body becomes a `{"role": "tool", ...}` message in the history. Next turn, the model has those instructions in context and follows them.

It's a self-serve instruction loader. The model decides when to reach for a skill.

## Key decisions

### Decision 1: skills are markdown, not Python

Same reason agents are markdown (step 00): edit-ability and transportability. Someone else's agent framework can read the same `SKILL.md` files. You can check them into git, share them on GitHub, copy them between projects. They're data, not code.

### Decision 2: one `skill` tool, many skills

Instead of registering N tools (one per skill), we register ONE tool that takes a skill name. This keeps the tool schema small even if you have 50 skills.

The tradeoff: the model only knows a skill's name and description until it calls `skill(skill_name=...)` and reads the body. So descriptions matter — they're the only thing the model sees before deciding whether to reach for the skill. A skill description that says "does stuff" is useless. A description that spells out when to invoke it gets used correctly.

### Decision 3: skills are opt-in per agent

Look at `agent.py`'s `_build_tools`:

```python
registry = ToolRegistry.with_builtins()

if self.agent_def.allow_skills:
    skill_tool = create_skill_tool(self.skill_loader)
    if skill_tool:
        registry.register(skill_tool)
```

Only agents with `allow_skills: true` in their `AGENT.md` frontmatter get the `skill` tool. This is deliberate — you probably don't want every agent to have access to every skill. A "customer support" agent might have the skills it needs; a "finance" agent, a different set.

Check `default_workspace/agents/pickle/AGENT.md` for the flag. Flip it off, restart, and the `skill` tool disappears.

### Decision 4: skills load at session start, not on demand

`create_skill_tool` scans `default_workspace/skills/` when the session is created and builds the tool schema then. If you add a new skill while the agent is running, it doesn't show up until the next session.

This is a pragmatic choice — reloading the tool schema mid-conversation is possible but noisy (the model would see the schema change). Step 08 (`08-config-hot-reload`) builds on this with a more sophisticated hot-reload pattern.

## Read the code

### 1. `src/mybot/core/skill_loader.py` — discovery

```python
class SkillLoader:
    def discover_skills(self) -> list[SkillDef]:
        return discover_definitions(
            self.config.skills_path, "SKILL.md", self._parse_skill_def
        )

    def _parse_skill_def(self, def_id, frontmatter, body) -> SkillDef | None:
        try:
            return SkillDef(
                id=def_id,
                name=frontmatter["name"],
                description=frontmatter["description"],
                content=body.strip(),
            )
        except (ValidationError, KeyError):
            return None

    def load_skill(self, skill_id: str) -> SkillDef:
        for skill in self.discover_skills():
            if skill.id == skill_id:
                return skill
        raise DefNotFoundError("skill", skill_id)
```

`discover_definitions` (shared with `AgentLoader` from step 00) walks the skills directory, finds every `SKILL.md`, parses frontmatter + body, and hands them to a callback. Bad skills are warned and skipped — they don't crash the whole agent.

`SkillDef` is a pydantic model with `extra="forbid"`, so unknown frontmatter keys raise a validation error. That's intentional: if someone misspells `descrption`, they get a clear error at load time.

### 2. `src/mybot/tools/skill_tool.py` — the dynamic tool

This is the interesting part. A factory function that **builds a tool at runtime** based on the skills it discovers:

```python
def create_skill_tool(skill_loader: "SkillLoader"):
    skill_metadata = skill_loader.discover_skills()

    if not skill_metadata:
        return None  # no skills, no tool

    # Build an XML-flavored description listing every skill
    skills_xml = "<skills>\n"
    for meta in skill_metadata:
        skills_xml += f'  <skill name="{meta.name}">{meta.description}</skill>\n'
    skills_xml += "</skills>"

    # The enum constrains the model to only pick a real skill id
    skill_enum = [meta.id for meta in skill_metadata]

    @tool(
        name="skill",
        description=f"Load and invoke a specialized skill. {skills_xml}",
        parameters={
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "enum": skill_enum,
                    "description": "The name of the skill to load",
                }
            },
            "required": ["skill_name"],
        },
    )
    async def skill_tool(skill_name: str, session) -> str:
        try:
            skill_def = skill_loader.load_skill(skill_name)
            return skill_def.content
        except Exception:
            return f"Error: Skill '{skill_name}' not found."

    return skill_tool
```

Two non-obvious things:

- **The description carries the skill catalog inline.** XML-flavored because the model reads it as structured data rather than flowing prose. This is prompt-engineering inside a tool description.
- **`enum` in the parameters** constrains what the model can pass. Even if the model hallucinates a skill name, the schema validation usually catches it before the tool runs.

### 3. `src/mybot/core/agent.py` — register the skill tool

Small change from step 01:

```python
def _build_tools(self) -> ToolRegistry:
    registry = ToolRegistry.with_builtins()
    if self.agent_def.allow_skills:
        skill_tool = create_skill_tool(self.skill_loader)
        if skill_tool:
            registry.register(skill_tool)
    return registry
```

The rest of the agent (the while-loop, the history, the LLM wrapper) is unchanged. All this step adds is "if skills are allowed, put a `skill` tool in the registry."

## Try it out

Make a skill:

```bash
mkdir -p ../default_workspace/skills/haiku
cat > ../default_workspace/skills/haiku/SKILL.md <<'EOF'
---
name: Haiku
description: Respond in 5-7-5 haiku form. Load when the user asks for a haiku, poetry, or wants a response in 17 syllables.
---

Every response must be a haiku:
- Line 1: exactly 5 syllables
- Line 2: exactly 7 syllables
- Line 3: exactly 5 syllables
- No other text. No explanation.
EOF
```

Then:

```bash
uv run my-bot chat
```

```
You: write me a haiku about tabs vs spaces

pickle: [calls skill(skill_name="haiku"), then responds]

       Tabs and spaces fight
       Pressing keys a thousand times
       Still indent feels wrong
```

## Exercises

1. **Watch the skill get invoked.** Add `print(f"Skill tool called with: {skill_name}")` inside `skill_tool()`. Send a message that should trigger the haiku skill. Watch the sequence: first turn, model calls `skill`; second turn, model follows the loaded instructions.

2. **Add a skill the agent can't see.** Make a skill with a useless description like `description: stuff`. Observe: the model ignores it. Useful descriptions are doing real work.

3. **Two skills that conflict.** Add a second skill that says "respond in limericks." Send "write me a poem." Watch the model pick one.

4. **Give your agent no skills.** Set `allow_skills: false` in `AGENT.md`. The `skill` tool disappears from the schema. Send the haiku request again — the model will attempt it without the skill's instructions and produce worse output.

## What breaks next

You have tools and skills now, but every conversation starts fresh. Quit the CLI, run it again, and your agent forgets everything.

Step 03 (`03-persistence`) fixes that with a `HistoryStore` that writes conversations to disk.

## What's Next

[Step 03: Persistence](../03-persistence/) — your agent remembers you between runs.
