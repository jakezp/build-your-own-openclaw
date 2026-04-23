"""CLI for the bootstrap template: `login` + a one-shot `chat`.

No session state, no history, no tools. Just the primitives from oauth.py
and responses.py wired into a two-command CLI.

Rename `yourpkg` throughout when you clone this into your own project.
"""

import asyncio

import typer
from rich.console import Console

from yourpkg.provider.llm.oauth import ChatGPTOAuth
from yourpkg.provider.llm.responses import (
    ResponsesClient,
    ResponsesRequest,
    aggregate_stream,
)

MODEL = "gpt-5.4"
INSTRUCTIONS = "You are a helpful assistant."

app = typer.Typer(
    name="yourbot",
    help="A ChatGPT-OAuth-backed minimal agent starter",
    no_args_is_help=True,
    add_completion=True,
)

console = Console()


@app.command("login")
def login() -> None:
    """Run one-time ChatGPT OAuth login and write the Token_Store."""
    result = ChatGPTOAuth().login()
    console.print(
        f"[green]Logged in as[/green] {result.account_id or '<unknown>'}\n"
        f"Token store: [cyan]{result.token_store_path}[/cyan]"
    )


@app.command("chat")
def chat(message: str = typer.Argument(..., help="Your message")) -> None:
    """Send one message to the model and print the reply.

    One-shot. No session history, no follow-ups. Grow this into an
    AgentSession when you need continuity; see step 00 of the tutorial
    for the pattern.
    """

    async def _run() -> None:
        oauth = ChatGPTOAuth()
        access_token = await oauth.access_token()
        account_id = await oauth.account_id()

        client = ResponsesClient()
        request = ResponsesRequest(
            model=MODEL,
            instructions=INSTRUCTIONS,
            input=[{"role": "user", "content": message}],
        )
        events = client.stream(
            request, access_token=access_token, account_id=account_id
        )
        aggregated = await aggregate_stream(events)
        console.print(aggregated.content)

    asyncio.run(_run())


if __name__ == "__main__":
    app()
