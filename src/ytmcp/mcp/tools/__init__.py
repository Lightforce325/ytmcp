"""Tool registry: aggregate and register all MCP tools."""

from __future__ import annotations

from typing import Any

from . import analytics_tools, comment_tools, playlist_tools, video_tools

_ALL_MODULES = (video_tools, playlist_tools, comment_tools, analytics_tools)


def register_all_tools(mcp: Any) -> None:
    """Register every ytmcp tool with the given MCP server."""
    for module in _ALL_MODULES:
        module.register(mcp)


__all__ = ["register_all_tools"]
