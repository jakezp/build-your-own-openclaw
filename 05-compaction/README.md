# Step 05: Compaction

> Long histories fit in short context windows.

## Prerequisites

- Steps 00–04 done.

```bash
cd 05-compaction
uv sync
```

## Why this step exists

You're re-sending the full history every turn (step 00, Decision 1). That's fine for conversations that stay short. It breaks when they don't.

Turn 50, you're paying to re-send 50 messages. Turn 200, you've exceeded the model's context window and the Responses API rejects the request entirely. Even before you hit the hard ceiling, you're wasting tokens on ancient chitchat the model doesn't need to remember.

**Compaction** is the answer: when the history gets too big, keep the recent part verbatim and replace the ancient part with a short summary. The model keeps recent conversational flow but loses the fine-grained detail of early turns — which is usually fine because those early turns were often small talk or set-up anyway.

This step introduces a `ContextGuard` that runs before every LLM call. Three-tier strategy: measure, truncate, summarize.

## The mental model

Every turn, before the LLM call, we ask:

1. **Is the history over the threshold?** We estimate tokens with a simple char-based heuristic (4 chars ≈ 1 token). Threshold: 160 000 tokens (80% of a 200k-token window).
2. If YES, can we fix it by **truncating oversized tool results**? Sometimes one tool call returned 50 KB of JSON. Chop it to 10 000 characters, add a marker, see if that fits.
3. If still too big, **summarize the oldest 50% of messages**. Keep the most recent 20% intact. Replace everything in between with a synthesized "here's what happened earlier" message.

Summarization uses the same LLM we're already paying for. Costs one extra call per compaction, but only when we'd otherwise hit the wall.

The agent code is barely changed — one line inside the chat loop:

```python
self.state = await self.context_guard.check_and_compact(self.state)
```

Inside `check_and_compact` is where the three-tier strategy lives.

## Key decisions

### Decision 1: proactive, not reactive

We check and compact **before** each LLM call, not after one fails. If we waited for a "context window exceeded" error, the user would see a failed turn. Compacting proactively means the worst case is a slow turn (summary call + chat call) not a broken turn.

### Decision 2: char-based token estimator

Earlier versions of this tutorial used `litellm.token_counter` — a real tokenizer. The OAuth Edition dropped `litellm`. Replacing it with a real tokenizer would require `tiktoken` (a heavy dependency).

Instead: `total_chars // 4`. It's a heuristic, systematically off by 20% depending on content. That's fine. Our threshold has its own margin (80% of 200k instead of 100%), and the whole point is "don't get anywhere near the ceiling." A 20%-off estimate is plenty.

### Decision 3: truncate tool results first, summarize second

Tool results are the biggest offenders. A `bash` call that runs `grep -r foo /large/directory` might produce megabytes. The LLM rarely needs the raw bulk — it needs the shape, maybe the first few lines, a signal that there's more.

Before summarizing, we try the cheap fix: any tool result over `max_tool_result_chars` (default 10 000) gets chopped with a marker. The model sees "truncated" and knows not to quote the missing part.

If the oversize is structural (long conversation, not one big blob), truncation alone won't get us under threshold. Then we summarize.

### Decision 4: summarization produces a fake user/assistant pair

When we compact, the summary becomes TWO messages in the history:

```python
{"role": "user", "content": f"[Previous conversation summary]\n{summary}"},
{"role": "assistant", "content": "Understood, I have the context."}
```

The fake pair makes the history continue to look like a conversation. A `{"role": "system"}` message would give us two system messages — awkward for the model to parse.

### Decision 5: `/compact` and `/context` slash commands

Step 04's pattern gives us two new local commands: `/compact` forces compaction now, `/context` shows current token usage vs threshold. Both bypass the LLM.

## Read the code

### 1. `src/mybot/core/context_guard.py` — the engine

The orchestration method is a linear decision tree:

```python
async def check_and_compact(self, state) -> SessionState:
    if self.estimate_tokens(state) < self.token_threshold:
        return state

    # Tier 1: truncate large tool results.
    state.messages = self._truncate_large_tool_results(state.messages)
    if self.estimate_tokens(state) < self.token_threshold:
        return state

    # Tier 2: summarize old messages.
    return await self._compact_messages(state)
```

`_compact_messages` is where the extra LLM call happens:

```python
compress_count = self._compress_message_count(state)
old_messages = state.messages[:compress_count]
old_text = self._serialize_messages_for_summary(old_messages)

response, _ = await state.agent.llm.chat(
    [{"role": "user", "content": f"Summarize the conversation so far...\n\n{old_text}"}],
    [],  # no tools — don't accidentally invoke anything during summarization
)

messages = [
    {"role": "user", "content": f"[Previous conversation summary]\n{response}"},
    {"role": "assistant", "content": "Understood, I have the context."},
]
messages.extend(state.messages[compress_count:])
state.messages = messages
```

The summarization call uses the same `LLMProvider.chat()` the main loop uses, but with NO tools and a fresh `messages=[...]` — we don't want the summary call to invoke tools or pick up context that's being summarized.

`_compress_message_count` decides the split: keep at least 4 recent messages (or 20% of the history), summarize at most 50%.

### 2. `src/mybot/core/agent.py` — one line inside the loop

`AgentSession` gets a new `context_guard` field. The `chat()` loop adds one line:

```python
while True:
    messages = self.state.build_messages()
    self.state = await self.context_guard.check_and_compact(self.state)  # NEW
    content, tool_calls = await self.agent.llm.chat(messages, tool_schemas)
    ...
```

The call happens INSIDE the tool-loop. A tool call that produces a huge result gets caught on the NEXT iteration of the while-loop, before we send the tool's output back to the model.

### 3. `src/mybot/core/commands/handlers.py` — `/compact`, `/context`

Both are one-method subclasses of `Command`. `/compact` calls `_compact_messages` directly, bypassing the threshold check. `/context` computes the current estimate and prints a gauge.

## Try it out

Lower the threshold artificially to see compaction fire quickly. Edit `05-compaction/src/mybot/core/agent.py` `_get_token_threshold` to return `2000`. Then:

```bash
uv run my-bot chat
```

```
You: tell me a five-paragraph story about sailors
pickle: [long story]
You: /context
Messages: 2
Tokens: 1,100 (55% of 2,000 threshold)

You: now tell me one about miners
pickle: [another long story]
You: /context
Messages: 4
Tokens: 2,200 (110% of 2,000 threshold)     # over! — next chat triggers compaction

You: and the third, about astronauts
pickle: [compaction runs transparently, then the story]
You: /context
Messages: 4
Tokens: ~1,400 (70%)                         # old messages summarized
```

Conversation stays usable as it grows. Put the threshold back to 160000 when you're done.

## Exercises

1. **Watch a compaction happen.** Add `print(f"COMPACTING: {len(state.messages)} msgs, {self.estimate_tokens(state)} tokens")` at the top of `_compact_messages`. Trigger the scenario above. Watch the log line fire exactly when expected.

2. **Tune the threshold per agent.** Add `max_tokens_threshold` to `LLMConfig` so `AGENT.md` frontmatter can set it per agent. Read it in `_get_token_threshold`. Different agents, different compaction budgets.

3. **Swap the summarization strategy.** Change `summary_prompt` to ask for "a list of key facts the user has shared" instead of a narrative summary. Does the model's next reply feel more or less informed about the ancient history?

4. **Find the edge case.** Can `_compact_messages` recurse infinitely? What happens if the summary itself is bigger than the threshold (because the model is very verbose)? Trace the code.

## What breaks next

You have the model, tools, skills, persistence, slash commands, compaction. What's missing?

Your agent can't reach the internet. All its tools are filesystem/shell — nothing that talks to HTTP services, nothing that reads live web pages. Step 06 adds `web_search` and `web_read` as tools, so the agent can actually answer "what's the weather in Seattle right now?"

## What's Next

[Step 06: Web Tools](../06-web-tools/) — your agent meets the internet.
