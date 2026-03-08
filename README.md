# Build Your Own OpenClaw

A step-by-step tutorial to build your own AI agent framework, from a simple chat loop to a production-ready multi-agent system.

## Overview

**18 progressive steps** that teach you how to build an AI agent framework like pickle-bot, featuring:
- Tool calling and skill learning
- Multi-platform support (CLI, Telegram, Discord, WebSocket)
- Event-driven architecture
- Multi-agent collaboration
- Scheduled autonomous tasks
- Long-term memory

**Target Audience:** Intermediate Python developers (async/await, classes)

**Example Project:** [pickle-bot](../pickle-bot/) - our reference implementation

## Tutorial Structure

### Phase 1: Capable Single Agent (Steps 1-8)
Build a fully-functional agent that can chat, use tools, learn skills, remember conversations, and access the internet.

- **00-chat-loop** - Your first agent
- **01-tools** - Agent can take actions
- **02-skills** - Dynamic capability loading
- **03-persistence** - Remember conversations
- **04-compaction** - Handle long conversations
- **05-slash-commands** - User control
- **06-config-hot-reload** - Edit without restart
- **07-web-tools** - Search and read the web

### Phase 2: Event-Driven Architecture (Steps 9-11)
Refactor to event-driven architecture for scalability and multi-platform support.

- **08-event-driven** - The Great Refactor
- **09-channels** - Multi-platform support
- **10-websocket-ui** - Real-time UI

### Phase 3: Autonomous & Multi-Agent (Steps 12-16)
Add scheduled tasks, agent collaboration, and intelligent routing.

- **11-cron-heartbeat** - Autonomous scheduled tasks
- **12-post-message-back** - Agent-initiated communication
- **13-agent-dispatch** - Agent collaboration
- **14-multi-layer-prompts** - Sophisticated agent configuration
- **15-channel-routing** - Right agent for right job

### Phase 4: Production & Scale (Steps 17-18)
Production features for reliability and long-term memory.

- **16-concurrency-control** - Rate limiting and concurrency
- **17-memory** - Long-term knowledge system

## How to Use This Tutorial

<!-- TODO: config api key config.user.yaml -->

1. **Start at step 00** - Each step builds on the previous
2. **Read the README** - Each step's README explains the feature and design decisions
3. **Review the code** - Check `src/` for complete implementation
4. **Run it yourself** - Each step is runnable and testable
5. **Compare with pickle-bot** - Reference implementation shows production version

## Implementation Priority

**CRITICAL: Follow this priority order when implementing each step:**

1. ⭐ **Match pickle-bot code first**
   - Copy from `../pickle-bot/src/picklebot/`
   - Use same classes, functions, patterns
   - Simplify if needed, but keep core logic

2. ⭐ **Match previous step second**
   - Start from previous step's `src/` folder
   - Make incremental changes only
   - Preserve structure and patterns

3. ⭐ **Write new code last**
   - Only if no reference exists in pickle-bot
   - Only if can't build on previous step
   - Document why new code was needed

**Why this matters:**
- pickle-bot is battle-tested production code
- Incremental changes are easier to understand
- Avoid reinventing the wheel

## Prerequisites

- Python 3.11+
- Understanding of async/await
- Basic understanding of LLMs (OpenAI API or similar)
- UV package manager (recommended)

## Quick Start

```bash
# Navigate to the tutorial
cd build-your-own-openclaw

# Start with step 00
cd 00-chat-loop

# Read the README
cat README.md

# Run the code
uv run python src/main.py chat
```

## Reference Implementation

This tutorial is based on [pickle-bot](../pickle-bot/), a production AI assistant framework. When in doubt, check the reference implementation!

## Project Structure

```
build-your-own-openclaw/
├── README.md              # This file (index)
├── PLAN.md                # Detailed implementation guide
├── 00-chat-loop/          # Step 0: Basic chat loop
│   ├── README.md
│   └── src/
├── 01-tools/              # Step 1: Add tools
│   ├── README.md
│   └── src/
├── ...                    # Steps 2-16
└── 17-memory/             # Step 17: Memory system
    ├── README.md
    └── src/
```

## Contributing

This tutorial is a work in progress. Each step is implemented in a separate session. Feel free to suggest improvements!
