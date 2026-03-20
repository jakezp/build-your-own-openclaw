# 步骤 04：斜杠命令

> 直接控制会话。

## 前置条件

与步骤 00 相同 - 复制配置文件并添加你的 API 密钥：

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
```

## 这节做什么

在聊天里输入 `/help`、`/skills`、`/session` 这类命令，直接执行确定性的功能。

### 架构

<img src="04-slash-commands.svg" align="center" width="100%" />

## 关键组件

- **Command**：斜杠命令的基类（异步 execute 方法）
- **CommandRegistry**：注册和派发命令
- **Commands**：`/help`、`/skills`、`/session`



[src/mybot/core/commands/base.py](src/mybot/core/commands/base.py) - 新文件

```python
class Command(ABC):
    """Base class for slash commands."""

    name: str
    aliases: list[str] = []
    description: str = ""

    @abstractmethod
    async def execute(self, args: str, session: "AgentSession") -> str:
        """Execute the command and return response string."""
        pass
```

[src/mybot/core/commands/registry.py](src/mybot/core/commands/registry.py) - 新文件

```python
class CommandRegistry:
    def register(self, cmd: Command) -> None:
        """Register a command and its aliases."""

    async def dispatch(self, input: str, session: "AgentSession") -> str | None:
        """Parse and execute a slash command. Returns None if not a command."""
```

[src/mybot/cli/chat.py](src/mybot/cli/chat.py) - 添加命令分发

```python
 async def run(self) -> None:
        # ... Say Hello
        while True:
            # ... Get user input

            # Check for slash commands
            cmd_response = await self.session.command_registry.dispatch(
                user_input, self.session
            )
            if cmd_response is not None:
                self.console.print(cmd_response)
                continue

            # Normal chat
            response = await self.session.chat(user_input)
            self.display_agent_response(response)

```

## 设计选择

斜杠命令要不要写进会话历史？两种都行：
- 不写：命令是控制，不是对话
- 写：方便回溯做了什么操作

看你的场景选。

## 试一试

```bash
cd 04-slash-commands
uv run my-bot chat

# Try the commands:
# You: /help
# **Available Commands:**
# /help, /? - Show available commands
# /skills - List all skills or show skill details
# /session - Show current session details

# You: /session
# **Session ID:** `abc123...`
# **Agent:** Pickle (pickle)
# **Created:** 2026-03-08T12:00:00
# **Messages:** 0
```

## 下一步

[步骤 05：压缩](../05-compaction/) - 继续聊天...
