# Implementation Plan - Build Your Own OpenClaw

This document provides detailed implementation guidance for each tutorial step, including specific pickle-bot code references and implementation notes.

## Implementation Principles

### **CRITICAL: Reuse pickle-bot Code**

- **Copy from pickle-bot** whenever possible - don't reinvent the wheel
- **Simplify only when needed** - remove production complexity, keep core logic
- **Reference paths** are relative to `../pickle-bot/src/picklebot/`
- **Keep it working** - each step must be runnable

### **Implementation Priority**

Follow this order when implementing each step:

1. ⭐ **Match pickle-bot code first**
   - Check `../pickle-bot/src/picklebot/` for existing implementation
   - Copy classes, functions, patterns from pickle-bot
   - Simplify if needed, but keep core logic
   - Reference the source file in comments

2. ⭐ **Match previous step second**
   - Start from previous step's `src/` folder
   - Make incremental changes only
   - Preserve structure and patterns
   - Show clear progression

3. ⭐ **Write new code last**
   - Only if no reference exists in pickle-bot
   - Only if can't build on previous step
   - Document why new code was needed
   - Keep it minimal and focused

### **Incremental Approach**

- Each step starts from previous step's codebase
- Add ONE major feature per step
- Show the evolution, not just the result
- **No test files** - one session per step for implementation

### **CRITICAL: Follow the Workflow**

See [WORKFLOW.md](./WORKFLOW.md) for the step-by-step implementation process.
This ensures consistency with picklebot patterns across all steps.

---

## Phase 1: Capable Single Agent

### Step 00: Chat Loop - Your First Agent

**Problem:** Need basic agent that can have a conversation

**What to Build:**
- Minimal AgentSession class
- Basic config (API key, model name)
- AGENT.md loader (just system prompt)
- CLI chat loop with typer

**Pickle-bot References:**
- `core/agent.py` - AgentSession class (lines 1-80 for minimal version)
- `core/config.py` - Config loading
- `cli/main.py` - CLI structure
- `core/agent_def.py` - AGENT.md parsing

**Key Code to Copy:**
```python
# From: core/agent.py
class AgentSession:
    def __init__(self, agent_def, provider):
        self.agent_def = agent_def
        self.provider = provider
        self.messages = []

    async def chat(self, user_input: str) -> str:
        # Add user message
        # Call LLM
        # Return response
```

**Files to Create:**
```
00-chat-loop/
├── README.md
└── src/
    ├── __init__.py
    ├── main.py (CLI)
    ├── agent.py (AgentSession)
    ├── config.py (basic config)
    ├── agent_def.py (AGENT.md loader)
    ├── provider.py (LLM provider)
    └── agents/
        └── default/
            └── AGENT.md
```

**Implementation Notes:**
- Keep AgentSession minimal - no tools, no history persistence yet
- Use litellm for multi-provider support (matches pickle-bot)
- AGENT.md only has `# System Prompt` section
- Config is just YAML with `llm.api_key` and `llm.model`

**Alternative Approaches:**
1. Use langchain instead of direct LLM calls (more abstraction)
2. Use dict config instead of AGENT.md (less file-based)
3. Use click instead of typer (more traditional CLI)
4. Use OpenAI SDK directly instead of litellm (simpler, less flexible)

---

### Step 01: Tools - Agent Can Take Actions

**Problem:** Agent can only talk, can't do anything

**What to Build:**
- ToolRegistry class
- Tool base class
- Basic tools (read, write, bash)
- Tool calling in AgentSession.chat()

**Pickle-bot References:**
- `tools/registry.py` - ToolRegistry
- `tools/base.py` - Tool base class
- `tools/bash.py`, `tools/read.py`, `tools/write.py` - Example tools
- `core/agent.py` - Tool calling logic (tool execution loop)

**Key Code to Copy:**
```python
# From: tools/registry.py
class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, tool: Tool):
        self.tools[tool.name] = tool

    async def call(self, name: str, **kwargs):
        return await self.tools[name].execute(**kwargs)
```

**Files to Add:**
```
01-tools/
├── src/
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py
│   │   ├── base.py
│   │   ├── read.py
│   │   ├── write.py
│   │   └── bash.py
│   └── agent.py (modified to call tools)
```

**Implementation Notes:**
- Tools are async functions with JSON schema
- Start with 3 simple tools (read, write, bash)
- Tool calling uses LLM function calling
- Add tool results to message history
- Handle tool call loop (agent may call multiple tools)

**Alternative Approaches:**
1. Use pydantic for tool schemas (more validation)
2. Use decorators for tool registration (less boilerplate)
3. Sync tools instead of async (simpler, less flexible)
4. Use langchain tools (more ecosystem, less control)

---

### Step 02: Skills - Dynamic Capability Loading

**Problem:** Tools are always loaded, need on-demand capabilities

**What to Build:**
- SkillLoader class
- SKILL.md format
- Skill loading tool
- On-demand skill loading

**Pickle-bot References:**
- `core/skills.py` - SkillLoader
- `skills/` directory - Example skills
- `tools/skill_loader.py` - Skill loading tool

**Key Code to Copy:**
```python
# From: core/skills.py
class SkillLoader:
    def load(self, skill_name: str) -> Skill:
        # Load SKILL.md
        # Parse instructions + tools
        # Return Skill object
```

**Files to Add:**
```
02-skills/
├── src/
│   ├── skills/
│   │   ├── __init__.py
│   │   ├── loader.py
│   │   └── base.py
│   ├── tools/
│   │   └── skill_loader.py (NEW)
│   └── skills/
│       └── example/
│           └── SKILL.md
```

**Implementation Notes:**
- Skills are loaded on-demand, not at startup
- SKILL.md has instructions + tool definitions
- Skill tool lets agent load skills dynamically
- Keep it simple - no skill dependencies yet
- Skill instructions are injected into system prompt

**Alternative Approaches:**
1. Pre-load all skills at startup (simpler, less dynamic)
2. Use Python modules instead of SKILL.md (more code, less declarative)
3. No skill system, only tools (simpler, less flexible)

---

### Step 03: Persistence - Remember Conversations

**Problem:** Agent forgets everything after restart

**What to Build:**
- HistoryStore class
- JSON-based session storage
- Session recovery on startup
- History manager

**Pickle-bot References:**
- `core/history.py` - HistoryStore
- `core/agent.py` - Session persistence logic

**Key Code to Copy:**
```python
# From: core/history.py
class HistoryStore:
    def save_session(self, session_id: str, messages: list):
        # Save to JSON file

    def load_session(self, session_id: str) -> list:
        # Load from JSON file
```

**Files to Add:**
```
03-persistence/
├── src/
│   ├── history.py
│   └── .sessions/
│       └── default.json
```

**Implementation Notes:**
- Use JSON for simplicity (pickle-bot uses same)
- Store in `.sessions/` directory
- Auto-recover last session on startup
- Add session_id to AgentSession
- Keep message history as list of dicts

**Alternative Approaches:**
1. Use SQLite instead of JSON (more structured, more complex)
2. Use pickle instead of JSON (Python-specific, harder to debug)
3. No persistence (simpler, but loses conversations)

---

### Step 04: Slash Commands - User Control

**Problem:** User has no way to control the conversation

**What to Build:**
- CommandRegistry
- Command handler
- Basic commands (/help, /clear, /compact, /reload)

**Pickle-bot References:**
- `core/commands/` - Command system
- `cli/main.py` - Command handling

**Key Code to Copy:**
```python
# From: core/commands/
class CommandRegistry:
    def register(self, name: str, handler: Callable):
        self.commands[name] = handler

    async def handle(self, command: str, session: AgentSession):
        await self.commands[command](session)
```

**Implementation Notes:**
- Commands start with /
- Handle in CLI loop before agent chat
- Start with 4-5 basic commands:
  - `/help` - Show available commands
  - `/clear` - Clear conversation
  - `/compact` - Manual compaction
  - `/reload` - Reload agent config
  - `/exit` - Exit chat
- Easy to add new commands later

**Alternative Approaches:**
1. No command system (simpler, less control)
2. Use CLI flags instead of slash commands (less interactive)
3. Use natural language commands (more flexible, less reliable)

---

### Step 05: Compaction - Handle Long Conversations

**Problem:** Context window fills up, can't have long conversations

**What to Build:**
- ContextGuard class (token counting)
- Message compaction strategy
- Keep recent + important messages
- Compaction triggers

**Pickle-bot References:**
- `core/context_guard.py` - ContextGuard
- `core/agent.py` - Compaction logic

**Key Code to Copy:**
```python
# From: core/context_guard.py
class ContextGuard:
    def should_compact(self, messages: list) -> bool:
        # Check token count

    def compact(self, messages: list) -> list:
        # Keep recent + important
```

**Implementation Notes:**
- Use tiktoken for token counting (matches pickle-bot)
- Keep last N messages (simple strategy)
- Summarize old messages (optional, requires LLM call)
- Add /compact command for manual trigger
- Check token count before each LLM call

**Alternative Approaches:**
1. No compaction, just fail (simpler, limited conversations)
2. Summarize all old messages (more context, more tokens)
3. Use sliding window only (no summarization)
4. Use semantic search to find important messages (more complex)

---

### Step 06: Web Tools - Access the Internet

**Problem:** Agent can't search the web or read URLs

**What to Build:**
- WebSearchProvider (Brave Search)
- WebReadProvider (Crawl4AI)
- Web tools (search, read)

**Pickle-bot References:**
- `provider/web_search/` - Search implementation
- `provider/web_read/` - Read implementation
- `tools/web.py` - Web tools

**Implementation Notes:**
- Use Brave Search API (free tier available)
- Use Crawl4AI for reading web pages
- Add web tools to ToolRegistry
- Requires API keys in config
- Handle rate limiting and errors

**Alternative Approaches:**
1. Use Google Search API (more results, requires API key)
2. Use requests + BeautifulSoup for reading (simpler, less robust)
3. No web tools (simpler, less capable)

---

## Phase 2: Event-Driven Architecture

### Step 07: Event-Driven - The Great Refactor

**Problem:** Direct calls make it hard to scale and add features

**What to Build:**
- Event types (InboundEvent, OutboundEvent, EventSource)
- EventBus (pub/sub)
- Base Worker class
- AgentWorker

**Pickle-bot References:**
- `core/events.py` - Event types
- `core/eventbus.py` - EventBus
- `server/worker.py` - Base Worker
- `server/agent_worker.py` - AgentWorker

**Implementation Notes:**
- This is the BIG REFACTOR
- Replace direct chat() calls with events
- Show before/after comparison
- Keep it working throughout refactor
- EventBus uses asyncio.Queue for async processing
- Workers run as background tasks

**Alternative Approaches:**
1. Keep direct calls (simpler, less scalable)
2. Use message queue (Redis, RabbitMQ) (more complex, more scalable)
3. Use callback pattern (simpler, less flexible)

---

### Step 08: Config Hot Reload

**Problem:** Need to restart to test config changes

**What to Build:**
- File watcher (watchdog)
- Reload config.*.yaml on change
- Hot reload trigger
- EventBus integration for reload notifications

**Pickle-bot References:**
- `utils/config_watcher.py` - File watching
- `core/config.py` - Config reloading

**Implementation Notes:**
- Use watchdog library for file watching
- Watch config.*.yaml files for changes
- Reload config on file change
- Emit event on config change via EventBus
- Notify agent of reload in chat
- Keep session history on reload

**Alternative Approaches:**
1. No hot reload, always restart (simpler, slower development)
2. Polling instead of file watcher (simpler, less efficient)
3. Manual reload command only (less convenient)

---

### Step 09: Channels - Multi-Platform Support

**Problem:** Agent only accessible via CLI

**What to Build:**
- Channel abstraction (EventSource subclasses)
- CLI channel
- Telegram channel
- Disk event persistence

**Pickle-bot References:**
- `channel/` - Channel implementations
- `channel/cli.py` - CLI channel
- `channel/telegram.py` - Telegram channel
- `core/eventbus.py` - Event persistence

**Implementation Notes:**
- Each platform is a channel
- Channels emit InboundEvents
- Add disk persistence for events (SQLite or JSON)
- Test with 2 platforms (CLI + Telegram)
- Handle platform-specific formatting

**Alternative Approaches:**
1. CLI only (simpler, less useful)
2. No channel abstraction, separate implementations (less clean)
3. Use bot framework (more abstraction, less control)

---

### Step 10: WebSocket UI - Real-Time Interface

**Problem:** Need visual interface and real-time updates

**What to Build:**
- WebSocketWorker
- WebSocket server
- Simple HTML/JS UI
- Event streaming

**Pickle-bot References:**
- `server/websocket_worker.py` - WebSocket worker
- `api/app.py` - WebSocket endpoints

**Implementation Notes:**
- Use fastapi WebSocket for server
- Stream events to connected clients
- Simple HTML/JS client (single file)
- Show agent status in real-time
- Handle multiple concurrent connections

**Alternative Approaches:**
1. No UI, CLI only (simpler, less visual)
2. Use SSE instead of WebSocket (simpler, less real-time)
3. Build full React/Vue frontend (more complex, more features)

---

## Phase 3: Autonomous & Multi-Agent

### Step 11: Cron + Heartbeat - Scheduled Tasks

**Problem:** Agent only responds, never initiates

**What to Build:**
- CronWorker
- CRON.md definitions
- Scheduled agent invocations
- Heartbeat monitoring

**Pickle-bot References:**
- `server/cron_worker.py` - Cron worker
- `core/cron.py` - CRON.md parsing

**Implementation Notes:**
- Use croniter for parsing cron syntax
- CRON.md defines schedules and prompts
- Emit InboundEvent on schedule
- Add heartbeat for health checks
- Store last run times

**Alternative Approaches:**
1. No scheduled tasks (simpler, less autonomous)
2. Use system cron (less portable, less integrated)
3. Use APScheduler (more features, more complexity)

---

### Step 12: Post Message Back - Agent-Initiated Communication

**Problem:** Agent can't initiate outbound messages

**What to Build:**
- PostMessageBackTool
- Tool that creates OutboundEvent
- Agent can send messages proactively

**Pickle-bot References:**
- `tools/post_message_back.py` - Implementation

**Implementation Notes:**
- Tool creates OutboundEvent
- DeliveryWorker handles it
- Agent can notify user proactively
- Useful for cron task results
- Support multiple channels

**Alternative Approaches:**
1. No proactive messaging (simpler, less autonomous)
2. Use separate notification system (less integrated)
3. Only allow responses to inbound messages (less flexible)

---

### Step 13: Agent Dispatch - Multi-Agent Collaboration

**Problem:** Need specialized agents working together

**What to Build:**
- DispatchEvent
- Subagent tool
- Dispatch result handling
- Agent-to-agent communication

**Pickle-bot References:**
- `core/events.py` - DispatchEvent
- `tools/subagent.py` - Subagent tool
- `server/agent_worker.py` - Dispatch handling

**Implementation Notes:**
- Agent can call other agents via tool
- DispatchEvent → InboundEvent for subagent
- Collect results from subagent
- Show agent collaboration example
- Handle dispatch timeouts

**Alternative Approaches:**
1. Single agent only (simpler, less specialized)
2. Manual agent switching (less automated)
3. No agent-to-agent communication (less collaboration)

---

### Step 14: Multi-Layer Prompts

**Problem:** Single AGENT.md isn't flexible enough

**What to Build:**
- SOUL.md (personality)
- MEMORY.md (persistent knowledge)
- Prompt composition
- Layer loading

**Pickle-bot References:**
- `core/agent_def.py` - Multi-file loading

**Implementation Notes:**
- AGENT.md = base prompt (capabilities, tools)
- SOUL.md = personality layer (tone, style)
- MEMORY.md = knowledge layer (user info)
- Compose all layers at load time
- Show in AGENT.md what layers exist

**Alternative Approaches:**
1. Single AGENT.md only (simpler, less organized)
2. Use database for memory (more complex, more structured)
3. Use config file instead of markdown (less readable)

---

### Step 15: Channel Routing - Right Agent for Right Job

**Problem:** All platforms go to same agent

**What to Build:**
- Routing system
- Binding patterns (regex)
- Source → agent mapping
- Default agent fallback

**Pickle-bot References:**
- `core/routing.py` - Routing logic
- `core/config.py` - Routing config

**Implementation Notes:**
- Regex patterns for routing
- Route by source type or content
- Config-based routing rules
- Show multi-agent usage (different agents for different channels)
- Handle routing conflicts

**Alternative Approaches:**
1. Single agent for all channels (simpler, less specialized)
2. Manual agent selection (less automated)
3. Round-robin routing (less intelligent)

---

## Phase 4: Production & Scale

### Step 16: Concurrency Control

**Problem:** Too many concurrent requests overwhelm system

**What to Build:**
- Rate limiting (per agent, per channel)
- Concurrent execution control
- Queue management
- Semaphore-based limiting

**Pickle-bot References:**
- `server/agent_worker.py` - Concurrency control

**Implementation Notes:**
- Use asyncio.Semaphore for limiting
- Per-agent limits (configurable)
- Per-channel limits (configurable)
- Queue management for pending requests
- Show metrics (queue depth, active tasks)

**Alternative Approaches:**
1. No limits (simpler, can overwhelm)
2. Global limit only (less granular)
3. Use external rate limiter (Redis) (more complex, distributed)

---

### Step 17: Memory - Long-Term Knowledge

**Problem:** Agent doesn't remember user preferences or long-term info

**What to Build:**
- Memory structure (topics, projects, daily-notes)
- Memory agent (specialized)
- Memory tools (store/retrieve/search)
- Memory integration

**Pickle-bot References:**
- `tools/memory.py` - Memory tools
- Memory agent definition

**Implementation Notes:**
- Memory agent is specialized for managing knowledge
- Store in structured format (topics, projects, etc.)
- Search and retrieve tools
- Main agent can query memory via dispatch
- Persist memory to disk

**Alternative Approaches:**
1. No memory system (simpler, less personalized)
2. Use database for memory (more structured, more complex)
3. Use vector database for semantic search (more advanced, more complex)

---

## File Organization

```
build-your-own-openclaw/
├── README.md (index)
├── PLAN.md (this file)
├── 00-chat-loop/
│   ├── README.md
│   └── src/
├── 01-tools/
│   ├── README.md
│   └── src/
... (one folder per step)
└── 17-memory/
    ├── README.md
    └── src/
```

Each step folder contains a snapshot of the codebase at that step.

## Session Workflow

Each step should be implemented in a separate session:

1. **Read this PLAN.md** - Understand what to build
2. **Check pickle-bot** - Find reference implementation
3. **Copy from previous step** - Start with last step's code
4. **Implement the feature** - Following priority order
5. **Write README.md** - Document the step
6. **Test it** - Make sure it runs
7. **Commit** - Save the snapshot

## Success Criteria

Each step is complete when:
- ✅ Code runs without errors
- ✅ Feature works as described
- ✅ README.md explains the step
- ✅ Code matches pickle-bot patterns
- ✅ Incremental from previous step
- ✅ No unnecessary new code
