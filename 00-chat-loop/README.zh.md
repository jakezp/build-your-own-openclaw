# 步骤 00：只是一个聊天循环

> 所有智能体都从一个简单的聊天循环开始。

## 前置条件

复制配置文件并添加你的 API 密钥：

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# 编辑 config.user.yaml 添加你的 API 密钥
```

## 这节做什么

AI 智能体的基础：一个简单的聊天循环，用户输入，LLM 响应。

<img src="00-chat-loop.svg" align="center" width="100%" />

## 关键组件

- **ChatLoop**：处理用户输入并显示响应
- **LLM Call**：将消息历史发送给 LLM 提供商并获取响应
- **Session**：管理对话状态和消息历史，LLM 始终看到完整历史

[src/mybot/cli/chat.py](src/mybot/cli/chat.py)

```python
class ChatLoop:
    async def run(self) -> None:
        self.console.print(
            Panel(
                Text("Welcome to my-bot!", style="bold cyan"),
                title="Chat",
                border_style="cyan",
            )
        )
        self.console.print("Type 'quit' or 'exit' to end the session.\n")

        try:
            while True:
                user_input = await asyncio.to_thread(self.get_user_input)

                if user_input.lower() in ("quit", "exit", "q"):
                    self.console.print("\n[bold yellow]Goodbye![/bold yellow]")
                    break

                if not user_input:
                    continue

                try:
                    response = await self.session.chat(user_input)
                    self.display_agent_response(response)
                except Exception as e:
                    self.console.print(f"\n[bold red]Error:[/bold red] {e}\n")

        except (KeyboardInterrupt, EOFError):
            self.console.print("\n[bold yellow]Goodbye![/bold yellow]")
```

[src/mybot/core/agent.py](src/mybot/core/agent.py)

``` python
class AgentSession:
    async def chat(self, message: str) -> str:
        user_msg: Message = {"role": "user", "content": message}
        self.state.add_message(user_msg)

        messages = self.state.build_messages()
        response = await self.agent.llm.chat(messages)

        assistant_msg: Message = {"role": "assistant", "content": response}
        self.state.add_message(assistant_msg)

        return response
```

[src/mybot/provider/llm/base.py](src/mybot/provider/llm/base.py)

``` python
class LLMProvider:
    async def chat(
        self,
        messages: list[Message],
        **kwargs: Any,
    ) -> str:
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "api_key": self.api_key,
        }

        if self.api_base:
            request_kwargs["api_base"] = self.api_base
        request_kwargs.update(kwargs)

        response = await acompletion(**request_kwargs)
        message = cast(Choices, response.choices[0]).message

        return message.content or ""
```


## 试一试

```bash
cd 00-chat-loop
uv run my-bot chat

# Type 'quit' or 'exit' to end the session.

# You: Hello, who is this?
# pickle: Meow! Hello there! I'm Pickle, your friendly cat assistant. 🐾
# You: I am Zane, Nice to meet you.
# pickle: Nice to meet you, Zane! *purrs happily* 🐱
```

## 下一步

[步骤 01：工具](../01-tools/) - 让智能体能真正做事
