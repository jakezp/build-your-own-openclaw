# Step 12: Cron Heartbeat

> Your agent wakes up on its own.

## Prerequisites

- Steps 00–11 done.

```bash
cd 12-cron-heartbeat
uv sync
```

## Why this step exists

Every previous step assumes a human prompts the agent. CLI, Telegram, Discord, WebSocket — all of them. The agent does nothing until someone types something.

What about work that should happen on a schedule? A daily news brief at 7am. A weekly report on Friday afternoons. A check-in every 15 minutes during business hours. Nothing to trigger these but a clock.

This step adds cron. Cron definitions live as markdown files in `workspace/crons/`. A `CronWorker` wakes up every minute, finds any jobs that are due, and dispatches each as an `InboundEvent`-shaped event on the bus (`DispatchEvent`). The agent handles it the same way it handles a message from Telegram: routing, session, chat.

The agent doesn't know or care that the request came from a schedule rather than a human.

## The mental model

A cron job is three things:

1. **A schedule** — standard 5-field cron expression (`0 7 * * *` = 7am every day).
2. **An agent** — which agent handles this job. Matches an `AGENT.md` id.
3. **A prompt** — the message the cron job sends on trigger. This becomes the user message the agent sees.

All three live in a single markdown file:

```markdown
---
name: Daily News
description: 7am news roundup
agent: scout
schedule: "0 7 * * *"
---

Summarize the top three news stories from the last 24 hours. Focus on tech and finance.
```

Every minute, the `CronWorker`:

1. Walks `workspace/crons/` and parses every `CRON.md`.
2. Checks each job's schedule against "now." Collects the ones that match.
3. For each due job, builds a fresh session, publishes a `DispatchEvent` to the bus.
4. The rest of the system — routing, session creation, agent dispatch — handles it from there.

Cron isn't scheduling work. It's producing events. The bus does the rest.

## Key decisions

### Decision 1: minute-granularity polling, not precise timers

`CronWorker.run()` is a `while True: await asyncio.sleep(60)` loop. We check every 60 seconds, match jobs whose schedule hits the current minute, dispatch.

Why not a precise timer that fires exactly at schedule time?

- A poll loop has one failure mode (missed tick, happens once per sleep). A timer has many (rescheduling after exceptions, leap-second quirks, aliased ticks).
- Minute granularity is enough for everything cron is good at. Sub-minute scheduling is a different problem (step 16's concurrency discussion nudges that way).

The cost: a job scheduled for `30 7 * * *` might fire anywhere from 7:30:00 to 7:30:59 wall clock. Not a problem for daily news briefs.

### Decision 2: 5-minute minimum schedule granularity

`CronDef.validate_schedule` enforces that consecutive runs of a cron expression must be at least 5 minutes apart:

```python
base = datetime(2024, 1, 1, 0, 0)
cron = croniter(v, base)
first_run = cron.get_next(datetime)
second_run = cron.get_next(datetime)
gap_minutes = (second_run - first_run).total_seconds() / 60

if gap_minutes < 5:
    raise ValueError(...)
```

A schedule like `* * * * *` (every minute) or `*/2 * * * *` (every 2 minutes) fails validation at load time.

Why 5 minutes? Because more frequent schedules are almost always a design mistake — someone meant "whenever" or "on demand," not a cron loop. If you genuinely need per-minute work, build a worker that subscribes to a timer event instead.

### Decision 3: cron jobs go through the normal event flow

When a cron fires, we publish a `DispatchEvent` — the same event `subagent_tool` (step 15) uses. The event has a `session_id`, a `source`, and a `content`. The `AgentWorker` already handles `DispatchEvent` (along with `InboundEvent`).

We don't introduce a "cron processor" separate from the event bus. Cron just becomes another thing that produces events. This means everything downstream — routing, compaction, persistence, tool use — works identically for cron jobs and human messages.

### Decision 4: `one_off: true` for self-deleting jobs

Sometimes you want a job to fire exactly once. A reminder in 30 minutes, a one-time research task. Add `one_off: true` to the frontmatter:

```markdown
---
name: Ping in 30 minutes
agent: pickle
schedule: "*/30 * * * *"       # fires every 30 minutes
one_off: true                  # ...but deletes itself after the first fire
---

Poke the user asking how their task is going.
```

The worker sees `cron_def.one_off`, dispatches the event, then `shutil.rmtree`s the cron directory. The cron is self-cleaning.

This makes cron usable for reminders without needing a separate "task scheduler" system.

### Decision 5: cron IS the agent

Inside the `CronDef.frontmatter`, `agent: scout` names the agent. The `CronWorker` loads that agent, creates a new session owned by it, and dispatches the prompt. The `RoutingTable` isn't consulted — the cron itself says which agent to use.

Why this choice? Because a cron is explicit. You wrote the prompt AND the agent in the same file. The author knows which agent they want. Cross-referencing a routing table would add ceremony for no benefit.

Contrast with step 11: routing matters when the SOURCE of the event doesn't carry agent information (a Telegram message, a WebSocket client — these say who they are, not which agent should handle them). Cron always says which agent.

## Read the code

### 1. `src/mybot/core/cron_loader.py` — parsing

`CronDef` is a pydantic model; the fields line up with the YAML frontmatter. The validator on `schedule` enforces cron validity and the 5-minute rule:

```python
@field_validator("schedule")
@classmethod
def validate_schedule(cls, v: str) -> str:
    if not croniter.is_valid(v):
        raise ValueError(f"Invalid cron expression: {v}")
    base = datetime(2024, 1, 1, 0, 0)
    cron = croniter(v, base)
    first_run = cron.get_next(datetime)
    second_run = cron.get_next(datetime)
    gap_minutes = (second_run - first_run).total_seconds() / 60
    if gap_minutes < 5:
        raise ValueError(
            f"Schedule must have minimum 5-minute granularity. Got: {v} "
            f"(runs every {gap_minutes:.0f} min)"
        )
    return v
```

`CronLoader.discover_crons()` uses the same machinery as `AgentLoader` / `SkillLoader` — walks the directory, parses, validates, returns a list.

### 2. `src/mybot/server/cron_worker.py` — the clock

`find_due_jobs` is a pure function — easy to unit-test, no globals:

```python
def find_due_jobs(jobs, now=None) -> list[CronDef]:
    now = now or datetime.now()
    now_minute = now.replace(second=0, microsecond=0)
    due_jobs = []
    for job in jobs:
        if croniter.match(job.schedule, now_minute):
            due_jobs.append(job)
    return due_jobs
```

`croniter.match(schedule, time)` returns True iff `time` is an exact match for the cron expression. We pass the current minute (seconds zeroed out) so "12:35:42" and "12:35:00" are equivalent — only the minute matters.

`CronWorker.run` is a minute ticker:

```python
async def run(self) -> None:
    while True:
        try:
            await self._tick()
        except Exception as e:
            self.logger.error(f"Error in tick: {e}")
        await asyncio.sleep(60)
```

Exceptions in one tick don't stop the next. Cron is never allowed to crash the process.

`_tick` discovers, filters, dispatches:

```python
async def _tick(self) -> None:
    jobs = self.context.cron_loader.discover_crons()
    due_jobs = find_due_jobs(jobs)

    for cron_def in due_jobs:
        agent_def = self.context.agent_loader.load(cron_def.agent)
        agent = Agent(agent_def, self.context)
        cron_source = CronEventSource(cron_id=cron_def.id)
        session = agent.new_session(cron_source)

        event = DispatchEvent(
            session_id=session.session_id,
            source=CronEventSource(cron_id=cron_def.id),
            content=cron_def.prompt,
        )
        await self.context.eventbus.publish(event)

        if cron_def.one_off:
            cron_path = self.context.cron_loader.config.crons_path / cron_def.id
            shutil.rmtree(cron_path)
```

Discover on every tick — so editing a CRON.md mid-run picks up the next minute. Add a new cron file, no restart needed.

## Try it out

Create a minimal cron:

```bash
mkdir -p ../default_workspace/crons/every-five
cat > ../default_workspace/crons/every-five/CRON.md <<'EOF'
---
name: Every Five
description: Test cron that fires every 5 minutes
agent: pickle
schedule: "*/5 * * * *"
---

Say hello, and include the current timestamp.
EOF
```

Run the server (step 12 has a `serve` command that starts the `CronWorker` alongside the other workers):

```bash
uv run my-bot serve
```

Within five minutes, you'll see log lines:

```
[CronWorker] Dispatched cron job: every-five
[AgentWorker] Session completed: <session-id>
```

Check the session's history. The first user message is your prompt, followed by the agent's reply. It ran while you did nothing.

## Exercises

1. **One-off reminder.** Make a cron with `one_off: true` scheduled for 5 minutes from now. Run the server. After it fires, check `crons/` — the job directory is gone.

2. **Break the schedule validator.** Try a CRON.md with `schedule: "* * * * *"` (every minute). Loading it should fail with the 5-minute message. Same for `schedule: "not-a-cron"`.

3. **Watch discovery happen live.** Start the server. Add a new `CRON.md` mid-run with a schedule that fires soon. No restart needed. It fires.

4. **Edit the discovery interval.** Change `asyncio.sleep(60)` to `asyncio.sleep(10)` temporarily. Watch the logs — tick now fires 6x per minute but due-job matching still only triggers at minute boundaries (the `now_minute` truncation). Why? Because crons schedule by minute; sub-minute polling buys nothing. Put 60 back.

## What breaks next

Cron means your agent does work without a human present. But the work's result — whatever the agent replies with — has no active user at a terminal waiting to see it. The cron fires at 7am; the user is asleep. When they wake up, they have no idea what happened.

Step 14 adds a `post_message` tool that lets the agent proactively send a message to a channel. Cron fires, agent does work, then calls `post_message` to land its findings in the user's Telegram chat. First, step 13 polishes the agent's prompt so it knows about its environment.

## What's Next

[Step 13: Multi-Layer Prompts](../13-multi-layer-prompts/) — more context, more context, more context.
