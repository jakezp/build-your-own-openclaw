# Step 16: Concurrency Control

> Too many Pickle is running at the same time?

## Prerequisites

Same as Step 09 - copy the config file and add your API key:

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# Edit config.user.yaml to add your API key
```

## What We Will Build

Some specialized agent will be problematic to run concurrently. We need some mechanism to limit this.

## Key Components

- **AgentDef.max_concurrency** - Configurable per-agent limit
- **Semaphore Based Concurrency Control** - Blocks when concurrency limit reached


[src/mybot/server/agent_worker.py](src/mybot/server/agent_worker.py)

```python
class AgentWorker(SubscriberWorker):
    def __init__(self, context: "SharedContext"):
        super().__init__(context)
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    async def exec_session(self, event, agent_def: "AgentDef") -> None:
        sem = self._get_or_create_semaphore(agent_def)

        async with sem:  # Blocks if limit reached
            # ... execute session ...

        self._maybe_cleanup_semaphores(agent_def)

    def _get_or_create_semaphore(self, agent_def: "AgentDef") -> asyncio.Semaphore:
        if agent_def.id not in self._semaphores:
            self._semaphores[agent_def.id] = asyncio.Semaphore(
                agent_def.max_concurrency
            )
        return self._semaphores[agent_def.id]
```

## Try it out

`Cookie` has `max_concurrency` as 1, dispatch from two different source (Non-cli) should trigger this. 

## Note

<!-- TODO mention concurrency control can be done differnelty, like by source, not by agetn. -->

## What's Next

[Step 17: Memory](../17-memory/) - Long-term knowledge system.
