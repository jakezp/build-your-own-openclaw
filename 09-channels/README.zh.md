# 步骤 09：频道

> 在手机上与你的智能体对话。

## 前置条件

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
# 配置 Telegram Bot Token
```
## 这节做什么

让智能体接入 Telegram、Discord 等平台。

<img src="09-channels.svg" align="center" width="100%" />

- 用户通过平台发送消息（Telegram、Discord）
- 频道接收消息并创建 EventSource
- ChannelWorker 将 InboundEvent 发布到 EventBus
- AgentWorker 处理事件并生成响应
- AgentWorker 将 OutboundEvent 发布到 EventBus
- DeliveryWorker 接收 OutboundEvent
- DeliveryWorker 查找会话的源并通过适当的频道发送

## 关键组件

- **EventSource** - 平台特定事件源的抽象基类（CLI、Telegram、Discord）
- **Channel** - 具有 run/reply/stop 接口的消息平台抽象基类
- **ChannelWorker** - 管理多个频道并发布 InboundEvents
- **DeliveryWorker** - 订阅 OutboundEvents 并通过适当的频道投递
- **Event Persistence** - 出站事件持久化和故障恢复，防止消息丢失


[src/mybot/channel/base.py](src/mybot/channel/base.py)

```python
class Channel(ABC, Generic[T]):
    @property
    @abstractmethod
    def platform_name(self) -> str:
        pass

    @abstractmethod
    async def run(self, on_message: Callable[[str, T], Awaitable[None]]) -> None:
        """Run the channel. Blocks until stop() is called."""
        pass

    @abstractmethod
    async def reply(self, content: str, source: T) -> None:
        """Reply to incoming message."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening and cleanup resources."""
        pass
```

[src/mybot/server/channel_worker.py](src/mybot/server/channel_worker.py)

```python
class ChannelWorker(Worker):
    async def run(self) -> None:
        channel_tasks = [
            channel.run(self._create_callback(channel.platform_name))
            for channel in self.channels
        ]
        await asyncio.gather(*channel_tasks)

    def _create_callback(self, platform: str):
        async def callback(message: str, source: EventSource) -> None:
            session_id = self._get_or_create_session_id(source)

            event = InboundEvent(
                session_id=session_id,
                source=source,
                content=message,
            )
            await self.context.eventbus.publish(event)

        return callback

    def _get_or_create_session_id(self, source: EventSource) -> str:
        source_session = self.context.config.sources.get(str(source))
        if source_session:
            return source_session.session_id

        agent_def = self.context.agent_loader.load(self.context.config.default_agent)
        agent = Agent(agent_def, self.context)
        session = agent.new_session(source)

        # Cache the session
        self.context.config.set_runtime(
            f"sources.{source}", SourceSessionConfig(session_id=session.session_id)
        )

        return session.session_id
```

- 每个 EventSource（例如 "platform-telegram:123:456"）映射到一个会话
- 第一条消息创建会话，后续消息复用它
- 会话 ID 缓存在 config.runtime.yaml 中

[src/mybot/server/delivery_worker.py](src/mybot/server/delivery_worker.py)

```python
class DeliveryWorker(SubscriberWorker):
    """Delivers outbound messages to platforms."""

    async def handle_event(self, event: OutboundEvent) -> None:
        """Handle an outbound message event."""
        session_info = self._get_session_source(event.session_id)
        source = self._get_delivery_source(session_info)

        if source and source.platform_name:
            channel = self._get_channel(source.platform_name)
            if channel:
                await channel.reply(event.content, source)

        self.context.eventbus.ack(event)
```

[src/mybot/core/eventbus.py](src/mybot/core/eventbus.py.py)

``` python
class EventBus(Worker):
    async def run(self) -> None:
        await self._recover()
        while True:
            # ... Dispatching Events

    async def _dispatch(self, event: Event) -> None:
        await self._persist_outbound(event)
        await self._notify_subscribers(event)

    async def _recover(self) -> int:
        pending_files = list(self.pending_dir.glob("*.json"))

        for file_path in pending_files:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            event = deserialize_event(data)
            await self._notify_subscribers(event)

        return len(pending_files)

    def ack(self, event: Event) -> None:
        filename = f"{event.timestamp}_{event.session_id}.json"
        final_path = self.pending_dir / filename
        if final_path.exists():
            final_path.unlink()
```

- **出站事件持久化流程**：
  - `EventBus.publish()` 将事件排队到内部 asyncio 队列
  - 对每个事件调用 `EventBus._dispatch()`
  - `_persist_outbound()` 原子地将 OutboundEvent 写入磁盘（tmp 文件 + fsync + 重命名）
  - `_notify_subscribers()` 将事件分发给所有订阅者（例如 DeliveryWorker）

- **故障恢复流程**：
  - EventBus 启动时，`_recover()` 扫描 pending 目录中的 `.json` 文件
  - 每个待处理事件被反序列化并重新分发给订阅者
  - 只有在成功投递后，DeliveryWorker 才调用 `eventbus.ack(event)`
  - `ack()` 删除持久化文件，确认投递完成

## 试一试

```bash
cd 09-channels
uv run my-bot server
# Send message from the channel of your choice.
```

## 下一步

[步骤 10：WebSocket](../10-websocket/)  - 用于与智能体交互的实时 Web 接口。
