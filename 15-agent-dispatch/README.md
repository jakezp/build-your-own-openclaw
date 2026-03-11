# Step 15: Agent Dispatch

Agents can now dispatch tasks to other specialized agents via the `subagent_dispatch` tool.

## Prerequisites

Same as previous steps - copy the config file and add your API key:

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# Edit config.user.yaml to add your API key
```

## What We Built

### Architecture

```
Agent A (caller)
      ↓
subagent_dispatch tool called
      ↓
DispatchEvent published to EventBus
      ↓
AgentWorker picks up DispatchEvent
      ↓
Agent B (subagent) processes task
      ↓
DispatchResultEvent published
      ↓
Agent A receives result
```

### Key Components

- **subagent_tool**: Factory that creates a dispatch tool with dynamic schema
- **AgentWorker**: Subscribes to both InboundEvent and DispatchEvent
- **DispatchEvent/DispatchResultEvent**: Event types for agent-to-agent communication

## Key Changes

[src/mybot/tools/subagent_tool.py](src/mybot/tools/subagent_tool.py) - NEW: Subagent dispatch tool

```python
def create_subagent_dispatch_tool(
    current_agent_id: str,
    context: "SharedContext",
) -> BaseTool | None:
    """Factory to create subagent dispatch tool with dynamic schema."""

    # Discover available agents, exclude current
    available_agents = context.agent_loader.discover_agents()
    dispatchable_agents = [a for a in available_agents if a.id != current_agent_id]

    if not dispatchable_agents:
        return None

    # Build description listing available agents
    agents_desc = "<available_agents>\n"
    for agent_def in dispatchable_agents:
        agents_desc += f'  <agent id="{agent_def.id}">{agent_def.description}</agent>\n'
    agents_desc += "</available_agents>"

    @tool(
        name="subagent_dispatch",
        description=f"Dispatch a task to a specialized subagent.\n{agents_desc}",
        parameters={...},
    )
    async def subagent_dispatch(
        agent_id: str, task: str, session: "AgentSession", context_arg: str = ""
    ) -> str:
        # Create new session for subagent
        # Publish DispatchEvent
        # Wait for DispatchResultEvent
        # Return result as JSON
```

[src/mybot/core/agent.py](src/mybot/core/agent.py) - Register subagent tool

```python
def _build_tools(self, include_post_message: bool) -> ToolRegistry:
    # ... other tools ...

    # Register subagent dispatch tool
    subagent_tool = create_subagent_dispatch_tool(
        self.agent_def.id, self.context
    )
    if subagent_tool:
        registry.register(subagent_tool)

    return registry
```

## How to Run

```bash
cd 15-agent-dispatch
uv run my-bot chat

# The subagent_dispatch tool is automatically available when:
# 1. Multiple agents exist in default_workspace/agents/
# 2. The current agent is not the only one

# Example: Ask the main agent to delegate to a specialist
> "Use the subagent_dispatch tool to ask the researcher agent to look up..."

# The tool schema dynamically lists available agents:
# - agent_id: enum of dispatchable agent IDs
# - task: the task description
# - context: optional additional context
```

## Benefits

1. **Specialization**: Route tasks to agents with specific expertise
2. **Dynamic Schema**: Tool description lists available agents at runtime
3. **Isolation**: Each subagent runs in its own session
4. **Async**: Dispatch is non-blocking, uses Future for result

## What's Next

[Step 16: Concurrency Control](../16-concurrency-control/) - Rate limiting and queue management
