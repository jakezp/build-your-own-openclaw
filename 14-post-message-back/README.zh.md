# 步骤 14：主动发消息

> 你的智能体想和你说话。

## 前置条件

与步骤 09 相同 - 复制配置文件并添加你的 API 密钥：

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
```

## 这节做什么

智能体可以主动给你发消息，不只是响应你。cron 任务里特别有用。

<img src="14-post-message-back.svg" align="center" width="100%" />

## 关键组件

- **post_message_tool** - 启用频道时创建工具的工厂
- **DeliveryWorker** - 处理 OutboundEvent 到平台的投递

[src/mybot/tools/post_message_tool.py](src/mybot/tools/post_message_tool.py)

```python
@tool(...)
async def post_message(content: str, session: "AgentSession") -> str:
    event = OutboundEvent(
        session_id=session.session_id,
        source=AgentEventSource(agent_id=session.agent.agent_def.id),
        content=content,
        timestamp=time.time(),
    )
    await context.eventbus.publish(event)
    return "Message queued for delivery"

return post_message
```

## 试一试

```bash
cd 14-post-message-back
uv run my-bot server

# From Channel of your choice:

# You: Say Hi to me after 5 minutes.
# pickle: I've scheduled a one-time "Hi" for you in about 2 minutes. You'll hear from me shortly! *purrs* ✅

# roughly 5 mins later

# pickle: Hi there! 👋 Just wanted to pop in and say hello! Hope you're having a wonderful day!
```

## 限制

`post_message` 工具只在 Cron 任务里能用。

## 下一步

[步骤 15：智能体调度](../15-agent-dispatch/) - 多智能体协作。
