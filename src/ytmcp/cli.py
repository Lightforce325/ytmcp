"""Command-line interface for ytmcp (Typer + Rich)."""

from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table

from .config import get_settings

app = typer.Typer(
    name="ytmcp",
    help="Unofficial YouTube API + MCP server CLI.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

auth_app = typer.Typer(help="Authentication commands.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")


# --------------------------------------------------------------------------- #
# Servers
# --------------------------------------------------------------------------- #
@app.command()
def serve() -> None:
    """Run the MCP server (stdio or http, per settings)."""
    from .mcp.server import main

    main()


@app.command()
def api() -> None:
    """Run the REST API server."""
    from .api.app import main as api_main

    api_main()


# --------------------------------------------------------------------------- #
# Channel / video commands
# --------------------------------------------------------------------------- #
@app.command()
def info(channel_id: str = typer.Argument(None, help="Channel id (omit for own channel).")) -> None:
    """Show channel information."""
    from .core import ChannelService, YouTubeClient

    async def _run() -> None:
        async with YouTubeClient() as client:
            info_obj = await ChannelService(client).get_info(channel_id)
            console.print_json(info_obj.model_dump_json())

    asyncio.run(_run())


@app.command()
def search(query: str, limit: int = typer.Option(10, help="Max results.")) -> None:
    """Search YouTube for videos."""
    from .core import SearchService, YouTubeClient

    async def _run() -> None:
        async with YouTubeClient() as client:
            videos = await SearchService(client).search(query, limit=limit)
            table = Table(title=f"Search: {query}")
            table.add_column("Video ID")
            table.add_column("Title")
            table.add_column("Views")
            for v in videos:
                table.add_row(v.video_id, v.title[:50], str(v.view_count or "-"))
            console.print(table)

    asyncio.run(_run())


@app.command()
def upload(
    file_path: str,
    title: str = typer.Option(..., help="Video title."),
    description: str = typer.Option("", help="Video description."),
    visibility: str = typer.Option("private", help="public|unlisted|private|scheduled"),
    tags: str = typer.Option("", help="Comma-separated tags."),
    thumbnail: str = typer.Option(None, help="Path to thumbnail image."),
) -> None:
    """Upload a video to the authenticated channel."""
    from .core import UploadController, YouTubeClient
    from .core.models import Visibility

    async def _run() -> None:
        async with YouTubeClient() as client:
            result = await UploadController(client).upload(
                file_path,
                title=title,
                description=description,
                visibility=Visibility(visibility.upper()),
                tags=[t.strip() for t in tags.split(",") if t.strip()],
                thumbnail_path=thumbnail,
            )
            console.print_json(result.model_dump_json())

    asyncio.run(_run())


@app.command()
def analytics(
    channel_id: str,
    period_days: int = typer.Option(28, help="Reporting window in days."),
) -> None:
    """Show channel analytics."""
    from .core import AnalyticsService, YouTubeClient

    async def _run() -> None:
        async with YouTubeClient() as client:
            data = await AnalyticsService(client).get_channel_analytics(
                channel_id, period_days=period_days
            )
            console.print_json(data.model_dump_json())

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
# Auth commands
# --------------------------------------------------------------------------- #
@auth_app.command("status")
def auth_status() -> None:
    """Show the currently resolved authentication mode."""
    from .core import AuthResolver

    ctx = AuthResolver().resolve()
    console.print(f"Auth mode: [bold]{ctx.mode}[/bold] (valid={ctx.is_valid})")


@auth_app.command("login")
def auth_login() -> None:
    """Start the OAuth device flow and print the user code."""
    from .core import AuthResolver, YouTubeClient

    async def _run() -> None:
        async with YouTubeClient() as client:
            provider = AuthResolver().oauth
            flow = await provider.start_device_flow(client)
            console.print(
                f"Go to [link]{flow.get('verification_url')}[/link] and enter code: "
                f"[bold yellow]{flow.get('user_code')}[/bold yellow]"
            )
            token = await provider.poll_device_token(
                client, flow["device_code"], interval=int(flow.get("interval", 5))
            )
            console.print(f"[green]Authenticated![/green] Scopes: {token.get('scope')}")

    asyncio.run(_run())


@auth_app.command("verify-cookies")
def auth_verify_cookies(
    cookie_file: str = typer.Argument(..., help="Path to cookies.txt / JSON."),
) -> None:
    """Verify that a cookie file contains an authenticated YouTube session."""
    from .core.auth import has_auth_cookies, load_cookie_file

    cookies = load_cookie_file(cookie_file)
    ok = has_auth_cookies(cookies)
    console.print(f"Cookies loaded: {len(cookies)}; authenticated: [bold]{ok}[/bold]")
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the ytmcp version."""
    from . import __version__

    console.print(__version__)


@app.command()
def config() -> None:
    """Print the resolved settings (secrets redacted)."""
    settings = get_settings()
    data = settings.model_dump(mode="json")
    for key in ("oauth_client_secret", "oauth_refresh_token"):
        if data.get(key):
            data[key] = "***"
    console.print_json(json.dumps(data))


if __name__ == "__main__":
    app()
