"""MCP server exposing ytmcp tools to AI agents.

Run with ``python -m ytmcp.mcp.server`` or the ``ytmcp-mcp`` entry point.
"""

from __future__ import annotations

import logging
from typing import Any

try:  # mcp >= 2.3 exposes MCPServer
    from mcp.server import MCPServer as _Server  # type: ignore[attr-defined]
except ImportError:  # mcp 2.x exposes FastMCP
    from mcp.server import FastMCP as _Server

from ..config import get_settings
from .tools import register_all_tools

logger = logging.getLogger(__name__)

_TRANSPORTS = {"stdio", "sse", "streamable-http"}


def create_server() -> Any:
    """Create and configure the ytmcp MCP server with all tools registered."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    mcp = _Server(
        "ytmcp",
        instructions=(
            "Control a YouTube channel end-to-end: upload/edit videos, manage "
            "playlists, moderate comments, and read analytics."
        ),
    )
    register_all_tools(mcp)
    return mcp


def main() -> None:
    """Entry point: run the MCP server over the configured transport."""
    settings = get_settings()
    mcp = create_server()
    transport: str = settings.mcp_transport
    if transport == "http":
        transport = "streamable-http"
    if transport not in _TRANSPORTS:
        transport = "stdio"
    logger.info("Starting ytmcp MCP server (transport=%s)", transport)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
