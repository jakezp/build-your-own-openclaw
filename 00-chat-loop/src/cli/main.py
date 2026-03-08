"""CLI interface for your-own-bot using Typer."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from src.cli.chat import chat_command
from src.utils.config import Config

app = typer.Typer(
    name="your-own-bot",
    help="Your-Own-Bot: Personal AI Assistant",
    no_args_is_help=True,
    add_completion=True,
)

console = Console()


def workspace_callback(ctx: typer.Context, workspace: str) -> Path:
    """Store workspace path in context for later use."""
    ctx.ensure_object(dict)
    ctx.obj["workspace"] = Path(workspace)
    return Path(workspace)


@app.callback()
def main(
    ctx: typer.Context,
    workspace: str = typer.Option(
        "../default_workspace",
        "--workspace",
        "-w",
        help="Path to workspace directory",
        callback=workspace_callback,
    ),
) -> None:
    """
    Configuration is loaded from workspace/config.user.yaml by default.
    Use --workspace to specify a custom workspace directory.
    """
    workspace_path = ctx.obj["workspace"]
    config_file = workspace_path / "config.user.yaml"

    if not config_file.exists():
        console.print(f"[yellow]No configuration found at {config_file}[/yellow]")
        raise typer.Exit(1)

    try:
        cfg = Config.load(workspace_path)
        ctx.obj["config"] = cfg
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1)


@app.command("chat")
def chat(
    ctx: typer.Context,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            "-a",
            help="Agent ID to use (overrides default_agent from config)",
        ),
    ] = None,
) -> None:
    """Start interactive chat session."""
    config = ctx.obj.get("config")
    chat_command(config, agent_id=agent)


if __name__ == "__main__":
    app()
