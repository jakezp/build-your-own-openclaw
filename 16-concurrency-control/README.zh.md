# 步骤 16：并发控制

> 太多 Pickle 同时运行？

## 前置条件

与步骤 09 相同 - 复制配置文件并添加你的 API 密钥：

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
```

## 这节做什么

限制同一智能体同时跑几个实例，防止资源爆掉。

<img src="16-concurrency-control.svg" align="center" width="100%" />

## 关键组件

- **AgentDef.max_concurrency** - 每个智能体可配置的限制
- **基于信号量的并发控制** - 达到并发限制时阻塞


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

## 试一试

`Cookie` 的 `max_concurrency` 设成 1，从两个不同源触发它。

## 并发控制粒度

- **按智能体**（本实现）：限制每种智能体同时跑几个
- **按源**：限制同一用户同时发几个请求
- **按优先级**：高优先级任务预留容量

## 下一步

[步骤 17：记忆](../17-memory/) - 长期知识系统。
