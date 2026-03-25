# 构建你自己的 OpenClaw

从简单的聊天循环开始，一步步搭建 [OpenClaw](https://github.com/openclaw/openclaw) 的精简版。

<img src="Cover.png" style="width: 100%;">

## 概述

18 个步骤，每步包含：

- 讲解关键组件和设计决策的 `README.md`
- 可直接运行的代码

**参考项目：** [pickle-bot](https://github.com/czl9707/pickle-bot)

## 教程结构

### 第一阶段：能干的单智能体（步骤 0-6）

让智能体学会聊天、用工具、加载技能、保存对话、上网搜索。

- [**00-chat-loop**](./00-chat-loop/) - 只是一个聊天循环
- [**01-tools**](./01-tools/) - 给你的智能体一个工具
- [**02-skills**](./02-skills/) - 用 `SKILL.md` 扩展你的智能体
- [**03-persistence**](./03-persistence/) - 保存你的对话
- [**04-slash-commands**](./04-slash-commands/) - 直接控制会话
- [**05-compaction**](./05-compaction/) - 打包历史，继续前进...
- [**06-web-tools**](./06-web-tools/) - 你的智能体想看看更大的世界

### 第二阶段：事件驱动（步骤 7-10）

换成事件驱动架构，支持多平台接入。

- [**07-event-driven**](./07-event-driven/) - 让你的智能体超越 CLI
- [**08-config-hot-reload**](./08-config-hot-reload/) - 无需重启即可编辑
- [**09-channels**](./09-channels/) - 在手机上与你的智能体对话
- [**10-websocket**](./10-websocket/) - 想要以编程方式与智能体交互？

### 第三阶段：自主与多智能体（步骤 11-15）

定时任务、智能路由、多智能体协作。

- [**11-multi-agent-routing**](./11-multi-agent-routing/) - 将正确的任务路由到正确的智能体
- [**12-cron-heartbeat**](./12-cron-heartbeat/) - 智能体在你睡觉时工作
- [**13-multi-layer-prompts**](./13-multi-layer-prompts/) - 更多上下文，更多上下文，更多上下文
- [**14-post-message-back**](./14-post-message-back/) - 你的智能体想和你说话
- [**15-agent-dispatch**](./15-agent-dispatch/) - 智能体调度，把活派给别的智能体

### 第四阶段：生产就绪（步骤 16-17）

并发控制和长期记忆。

- [**16-concurrency-control**](./16-concurrency-control/) - 太多 Pickle 同时运行？
- [**17-memory**](./17-memory/) - 记住我！

## 如何使用本教程

### 配置 API 密钥

1. 复制配置模板：
   ```bash
   cp default_workspace/config.example.yaml default_workspace/config.user.yaml
   ```

2. 编辑 `config.user.yaml` 填入 API 密钥：
   - [LiteLLM providers](https://docs.litellm.ai/docs/providers) 列出所有支持的模型提供商
   - [Provider Examples](PROVIDER_EXAMPLES.md) 有配置示例

## 贡献

每个步骤独立实现，欢迎提 PR。
