# 步骤 05：压缩

> 打包历史，继续前进...

## 前置条件

与步骤 00 相同 - 复制配置文件并添加你的 API 密钥：

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
```

## 这节做什么

聊久了上下文会爆。压缩就是把旧消息总结一下，滚动到新会话继续聊。

<img src="05-compaction.svg" align="center" width="100%" />

- 上下文超过阈值？
- 截断过大的工具结果。
- 仍然过大？
- 总结旧消息。
- 滚动到新会话。


## 关键组件

- **Token 估算**：用 litellm 的 token_counter
- **截断策略**：先截大工具结果，再总结旧消息
- **上下文压缩**：总结旧消息，作为新会话的前几个提示
- **命令**：`/compact` 手动压缩，`/context` 看使用量



[src/mybot/core/context_guard.py](src/mybot/core/context_guard.py) - 新文件

```python
@dataclass
class ContextGuard:
    token_threshold: int = 160000  # 80% of 200k context

    def estimate_tokens(self, state: SessionState) -> int:
        return token_counter(model=state.agent.agent_def.llm.model, messages=state.build_messages())

    async def check_and_compact(self, state: SessionState) -> SessionState:
        token_count =

        if self.estimate_tokens(state) < self.token_threshold:
            return state

        state.messages = self._truncate_large_tool_results(state.messages)

        if self.estimate_tokens(state) < self.token_threshold:
            return state

        return await self._compact_messages(state)
```

[src/mybot/core/agent.py](src/mybot/core/agent.py) - 集成

```python
async def chat(self, message: str) -> str:
    # ... add user message ...

    while True:
        messages = self.state.build_messages()
        # Check and compact before LLM call
        self.state = await self.context_guard.check_and_compact(self.state)
        content, tool_calls = await self.agent.llm.chat(messages, tool_schemas)
```

## 试一试

```bash
cd 05-compaction
uv run my-bot chat

# Check context usage anytime:
# You: /context
# **Messages:** 12
# **Tokens:** 15,420 (9.6% of 160,000 threshold)

# You: /compact
# ✓ Context compacted. 8 messages retained.
```

## 下一步

[步骤 06：Web 工具](../06-web-tools/) - 添加网络搜索和 URL 阅读功能
