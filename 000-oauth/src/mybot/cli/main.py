"""CLI interface for step 000: `my-bot login` only.

This step introduces the OAuth machinery in isolation. It exposes a
single command, ``my-bot login``, which runs the one-time PKCE browser
flow and writes the Token_Store.

Later steps (00-chat-loop onward) layer agents, sessions, and a
``my-bot chat`` command on top of the same Token_Store.
"""

import typer
from rich.console import Console

from mybot.provider.llm.oauth import ChatGPTOAuth

app = typer.Typer(
    name="my-bot",
    help="my-bot (step 000): ChatGPT subscription OAuth login",
    no_args_is_help=True,
    add_completion=True,
)

console = Console()


@app.callback()
def main() -> None:
    """my-bot CLI entry point.

    A no-op callback. Its presence forces Typer to treat the app as
    a command group rather than collapsing the single `login` command
    into the top-level invocation. Without this, `my-bot login` would
    become just `my-bot` (try it and see).
    """


@app.command("login")
def login() -> None:
    """Run one-time ChatGPT OAuth login and write the Token_Store.

    Opens your browser to the ChatGPT authorization server, binds a
    loopback callback on 127.0.0.1:1455, exchanges the authorization
    code for tokens, and writes the Token_Store (POSIX mode 0600).

    After this, every tutorial step reads the same Token_Store.
    """
    result = ChatGPTOAuth().login()
    console.print(
        f"[green]Logged in as[/green] {result.account_id or '<unknown>'}\n"
        f"Token store: [cyan]{result.token_store_path}[/cyan]"
    )


if __name__ == "__main__":
    app()
