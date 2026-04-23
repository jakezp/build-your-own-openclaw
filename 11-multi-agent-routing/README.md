# Step 11: Multi-Agent Routing

> Send the right job to the right agent.

## Prerequisites

- Steps 00–10 done.

```bash
cd 11-multi-agent-routing
uv sync
```

## Why this step exists

So far you have ONE agent per session. When an inbound event arrives, the `AgentWorker` looks up the session's stored agent id and uses whatever agent that session was created with. If you want a different agent, you start a different session.

That's limiting. Consider:

- You have multiple agents configured: `pickle` for general chat, `cookie` for memory tasks, `scout` for web research. You want inbound messages to go to the right one automatically based on where they come from (Telegram → pickle, a specific cron job → scout).
- You want a **new** source (a Telegram chat you haven't seen before) to get auto-bound to the right agent without manual config.
- You want bindings to survive restarts.

This step adds a `RoutingTable`: regex-based rules that match source identifiers to agent ids. The routing table is authoritative over "which agent handles this source" decisions. Session creation consults it. Cron jobs (step 12) consult it. The inbound event flow from every channel consults it.

## The mental model

Every inbound event has a **source** — a platform-specific identifier. For CLI, it might be `cli:jacquecp@host`. For Telegram, `telegram:12345`. For cron, `cron:daily-news-brief`. Each is a string.

A `Binding` is a `(regex, agent_id)` pair:

```yaml
routing:
  bindings:
    - agent: scout
      value: "cron:research-.*"
    - agent: cookie
      value: "subagent:memory"
    - agent: pickle
      value: ".*"           # catch-all
```

When a new source shows up, the routing table walks its bindings and returns the first agent whose regex matches. Most-specific first, catch-all last. If nothing matches, fall back to `config.default_agent`.

The table also caches the (source → session) mapping. First time `cli:jacquecp@host` shows up, we create a session and store `sources.cli:jacquecp@host.session_id` in `config.runtime.yaml`. Next time the same source shows up, we hand back the existing session id — conversation continues.

## Key decisions

### Decision 1: regex match, not exact match

A binding's `value` is compiled as a regex: `^{value}$`. This gives you:

- Exact strings: `"cli:jacquecp@host"` matches one source exactly.
- Prefix globs: `"cron:research-.*"` matches any cron job starting with `research-`.
- Catch-all: `".*"` matches anything.

Regex is overkill for simple cases but cheap for complex ones. A fnmatch glob or a prefix check would cover 90% of use cases, but the 10% ends up being "just let me write a regex." So we did.

### Decision 2: specificity tiers for ordering

When multiple bindings could match, which wins? Without an order rule, the first one in the config file wins — which is fragile (users will forget the order matters).

Each binding gets a **tier**:

- **Tier 0**: no regex metacharacters at all. An exact string. Most specific.
- **Tier 1**: has regex metacharacters but no `.*`. Specific patterns like `"cli:[a-z]+"`.
- **Tier 2**: contains `.*`. Typical catch-alls.

`_load_bindings()` sorts by `(tier, original_index)`. Exact strings match first, then specific patterns, then catch-alls. Within a tier, config order is preserved (so if you have two equally-specific bindings, the first-declared wins).

The cost: this adds surprise for anyone reading the config top-down ("why is this third binding matching first?"). The doc comment on `Binding._compute_tier` is the mitigation.

### Decision 3: learned bindings go to `config.runtime.yaml`

If an agent dispatches a subagent (step 15's `subagent_tool`), the subagent's source looks like `subagent:memory`. We want that to route to `cookie` forever after, without the user needing to edit `config.user.yaml`.

`persist_binding(source_pattern, agent_id)` appends to the bindings list and writes it to `config.runtime.yaml` — the file the agent itself owns (step 08, Decision 1). Next hot-reload, the new binding is live.

### Decision 4: (source, session_id) mapping cached in config

When a source shows up for the first time, we create a session. When the same source shows up again an hour later, we want it hooked into the same session (conversation continuity).

We cache the mapping in `config.sources`:

```yaml
# config.runtime.yaml
sources:
  "cli:jacquecp@host":
    session_id: "4e8f..."
  "telegram:12345":
    session_id: "9abc..."
```

`get_or_create_session_id(source)` checks this cache first; falls through to "resolve the agent, create a new session, cache it" if there's no hit.

Config isn't the most obvious place for this cache. We could have used a separate sqlite database, or an in-memory dict. We chose config because:

1. It's already persisted (step 08).
2. It already has hot reload — another worker can see the new mapping immediately.
3. There's no meaningful size concern — one line per source.

### Decision 5: binding cache invalidation by hash

`RoutingTable` caches the sorted binding list. On every call to `resolve()`, it hashes the current config's bindings and compares to the stored hash. If different, rebuild the cache.

Why a hash instead of a reload counter? Because config hot-reload doesn't notify subscribers directly — it updates the in-place `Config` object's fields (step 08, Decision 1). If we cached by reload count, we'd need a separate counter and a hook into reload. A hash is self-healing: it just works.

## Read the code

### 1. `src/mybot/core/routing.py` — the table

Three dataclasses, one composite method.

**`Binding`**: a `(regex, agent_id)` pair, plus a computed `tier`:

```python
@dataclass
class Binding:
    agent: str
    value: str
    tier: int = field(init=False)
    pattern: Pattern = field(init=False)

    def __post_init__(self):
        self.pattern = re.compile(f"^{self.value}$")
        self.tier = self._compute_tier()

    def _compute_tier(self) -> int:
        if not any(c in self.value for c in r".*+?[]()|^$"):
            return 0  # exact string
        if ".*" in self.value:
            return 2  # catch-all-ish
        return 1      # specific regex
```

**`RoutingTable`**: holds a `SharedContext`, caches sorted bindings, exposes `resolve()` and `get_or_create_session_id()`:

```python
def resolve(self, source: str) -> str:
    for binding in self._load_bindings():
        if binding.pattern.match(source):
            return binding.agent
    return self.context.config.default_agent

def get_or_create_session_id(self, source: EventSource) -> str:
    source_str = str(source)

    source_session = self.context.config.sources.get(source_str)
    if source_session:
        return source_session.session_id

    agent_id = self.resolve(source_str)
    agent_def = self.context.agent_loader.load(agent_id)
    agent = Agent(agent_def, self.context)
    session = agent.new_session(source)

    self.context.config.set_runtime(
        f"sources.{source_str}", SourceSessionConfig(session_id=session.session_id)
    )
    return session.session_id
```

`_load_bindings()` does the cache-or-rebuild dance using the config hash.

### 2. Call sites

The interesting thing is WHERE `RoutingTable` gets called:

- **Channel workers** (Telegram, Discord, CLI, WebSocket) use `get_or_create_session_id(source)` to turn a platform-specific source into a session id. They no longer need to know or care about agents.
- **`AgentWorker`** still loads the session's agent (from `history_store.get_session_info`). The routing table decided the agent at session-create time; once created, the session is locked to that agent.

So routing happens **on session creation**, not on every event. A source that already has a session stays with that session's agent. A source showing up for the first time consults the routing table.

## Try it out

Set up bindings in `default_workspace/config.user.yaml`:

```yaml
routing:
  bindings:
    - agent: cookie       # memory agent
      value: "subagent:memory"
    - agent: pickle       # general chat
      value: ".*"         # catch-all
```

Run chat:

```bash
uv run my-bot chat
```

It goes to `pickle` (the catch-all). Now in a second terminal, check `config.runtime.yaml` — there's a new `sources.cli:...` entry with the session id you're in.

Restart the CLI. Same user, same session — continuity preserved through the source cache.

## Exercises

1. **Add a per-channel binding.** Route Telegram to pickle, Discord to cookie. Your config:
   ```yaml
   routing:
     bindings:
       - agent: pickle
         value: "telegram:.*"
       - agent: cookie
         value: "discord:.*"
   ```
   Set up both channels. Watch different messages land on different agents.

2. **Observe tier sorting.** Add two bindings where one is more specific (`telegram:12345`) and the other is a catch-all (`telegram:.*`). Confirm specific beats catch-all. Flip their order in the yaml — confirm it still works (tier sort wins over config order).

3. **Persist a learned binding.** From a REPL: `routing_table.persist_binding("manual:foo", "cookie")`. Check `config.runtime.yaml` — the new binding is there. Restart. It's still there.

4. **Break the regex.** Add a binding with `value: "[bad regex"`. Restart — the RoutingTable will raise at compile time. This is your signal that bad regexes fail loud, not silent.

## What breaks next

You have multiple agents, smart routing, per-source sessions. But the agent still only runs when someone talks to it. It never does anything on its own.

Step 12 adds cron: the agent wakes up on a schedule and does work without waiting for a human.

## What's Next

[Step 12: Cron Heartbeat](../12-cron-heartbeat/) — your agent wakes up on its own.
