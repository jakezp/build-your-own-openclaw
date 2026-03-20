# 步骤 07：事件驱动架构

> 让你的智能体超越 CLI。

## 前置条件

与步骤 06 相同 - 复制配置文件并添加你的 API 密钥：

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
```

## 这节做什么

这步改动较大。用事件总线把消息源和智能体执行解耦，后面几步都依赖这个架构。

<img src="07-event-driven.svg" align="center" width="100%" />

## 关键组件

- **EventBus** - 用于事件分发的中心发布/订阅
- **Events** - InboundEvent、OutboundEvent
- **Workers** - 处理事件的后台任务
- **AgentWorker** - 处理 InboundEvent → 执行智能体会话 → 发出 OutboundEvent



[src/mybot/core/events.py](src/mybot/core/events.py)

```python
@dataclass
class InboundEvent(Event):
    session_id: str
    content: str
    retry_count: int = 0

@dataclass
class OutboundEvent(Event):
    session_id: str
    content: str
    error: str | None = None
```

[src/mybot/core/eventbus.py](src/mybot/core/eventbus.py)

```python
class EventBus(Worker):
    def subscribe(
        self, event_class: type[E], handler: Callable[[E], Awaitable[None]]
    ) -> None:
        """Subscribe a handler to an event class."""
        self._queue: asyncio.Queue[Event] = asyncio.Queue()

    def unsubscribe(self, handler: Handler) -> None:
        """Remove a handler from all subscriptions."""

    async def publish(self, event: Event) -> None:
        """Publish an event to the internal queue (non-blocking)."""
        await self._queue.put(event)

    async def run(self) -> None:
        """Process events from queue, starting with recovery."""
        logger.info("EventBus started")

        try:
            while True:
                event = await self._queue.get()
                try:
                    await self._dispatch(event)
                except Exception as e:
                    logger.error(f"Error dispatching event: {e}")
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            logger.info("EventBus stopping...")
            raise
```

[src/mybot/server/agent_worker.py](src/mybot/server/agent_worker.py)

```python
class AgentWorker(SubscriberWorker):
    def __init__(self, context):
        self.context.eventbus.subscribe(InboundEvent, self.dispatch_event)

    async def dispatch_event(self, event: InboundEvent):
        agent = Agent(agent_def, self.context)
        session = agent.resume_session(event.session_id)
        response = await session.chat(event.content)

        result = OutboundEvent(
            session_id=event.session_id,
            content=response,
        )
        await self.context.eventbus.publish(result)
```

## 试一试

跑起来和上一步一样，看不出区别。

急的读者直接跳 [步骤 09：频道](../09-channels/)。

## 下一步

[步骤 08：配置热重载](../08-config-hot-reload/) - 配置合并和配置热重载
