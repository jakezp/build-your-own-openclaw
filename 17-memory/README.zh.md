# 步骤 17：记忆

> 记住我！

## 前置条件

与步骤 09 相同 - 复制配置文件并添加你的 API 密钥：

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
```

## 这节做什么

长期记忆，跨会话记住用户信息。

```
pickle: @cookie Do you know <topic> about user?
cookie: Yes, <content>.
```

## 关键组件

- **Memory agent** - 专门用于记忆管理的智能体
- [default_workspace/agents/cookie/AGENT.md](../default_workspace/agents/cookie/AGENT.md)

## 试一试

```bash
cd 17-memory
uv run my-bot chat

# You: Remember that I my name is Zane
# Pickle: Got it! I've saved that preference.

uv run my-bot chat

# User: What's my name?
# Pickle: Based on your memory, you name is Zane! Hi Zane! 😸
```

## 实现方式对比

| 方法 | 描述 |
|------|------|
| **专用智能体**（本实现）| 通过调度访问的记忆智能体 |
| **内置工具**| 主智能体直接带记忆工具 |
| **基于技能**| 用 grep 等 CLI 工具 |
| **向量数据库**| embedding + 语义搜索 |

### 记忆目录结构（Pickle Bot）

```
memories/
├── topics/
│   ├── preferences.md    # 用户偏好
│   └── identity.md       # 用户信息
├── projects/
│   └── my-project.md     # 项目特定笔记
└── daily-notes/
    └── 2024-01-15.md     # 每日日志
```

## 下一步

部署、扩展和定制！
