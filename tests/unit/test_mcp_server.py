"""Tests for the MCP server and the tool registry.

Covers:
- ``create_server`` builds a server without error.
- exactly 20 tools are exposed, with the expected names.
- every tool has a non-empty description and a valid object input schema.
- ``register_all_tools`` is effectively idempotent (re-registering does not
  error and does not duplicate tools).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from ytmcp.mcp.server import _Server, create_server
from ytmcp.mcp.tools import register_all_tools

# The complete, expected tool surface exposed to AI agents.
EXPECTED_TOOLS = {
    # video
    "upload_video",
    "update_video_metadata",
    "set_video_visibility",
    "delete_video",
    "get_video_info",
    "search_videos",
    "list_channel_videos",
    # playlist
    "create_playlist",
    "list_playlists",
    "add_video_to_playlist",
    "remove_video_from_playlist",
    "delete_playlist",
    # comments
    "list_comments",
    "reply_to_comment",
    "moderate_comment",
    # channel & analytics
    "get_channel_info",
    "get_subscriber_count",
    "update_channel_branding",
    "get_analytics",
    "get_top_videos",
}


async def test_create_server_returns_server() -> None:
    """create_server() builds a server object without raising."""
    server = create_server()
    assert server is not None
    # It must be able to answer list_tools.
    tools = await server.list_tools()
    assert isinstance(tools, list)


async def test_list_tools_returns_exactly_20() -> None:
    """The server exposes exactly 20 tools."""
    server = create_server()
    tools = await server.list_tools()
    assert len(tools) == 20


async def test_tool_names_match_expected_set() -> None:
    """The set of tool names matches the expected surface exactly."""
    server = create_server()
    names = {t.name for t in await server.list_tools()}
    assert names == EXPECTED_TOOLS


async def test_tool_names_no_duplicates() -> None:
    """No duplicate tool names are registered."""
    server = create_server()
    tools = await server.list_tools()
    names = [t.name for t in tools]
    assert len(names) == len(set(names))


async def test_every_tool_has_non_empty_description() -> None:
    """Each tool exposes a non-empty description."""
    server = create_server()
    for tool in await server.list_tools():
        assert tool.description, f"{tool.name} has no description"
        assert tool.description.strip(), f"{tool.name} description is blank"


async def test_every_tool_has_object_input_schema() -> None:
    """Each tool has an object input schema with a properties mapping."""
    server = create_server()
    for tool in await server.list_tools():
        schema = tool.inputSchema
        assert isinstance(schema, dict), f"{tool.name} inputSchema is not a dict"
        assert schema.get("type") == "object", f"{tool.name} schema type != object"
        props = schema.get("properties")
        assert isinstance(props, dict), f"{tool.name} has no properties mapping"
        assert props, f"{tool.name} has empty properties"


async def test_upload_video_schema_exposes_expected_params() -> None:
    """Spot-check: upload_video declares its key parameters."""
    server = create_server()
    tool = next(t for t in await server.list_tools() if t.name == "upload_video")
    props = tool.inputSchema["properties"]
    for param in ("file_path", "title", "description", "visibility"):
        assert param in props, f"upload_video missing param '{param}'"
    required = tool.inputSchema.get("required", [])
    assert "file_path" in required
    assert "title" in required


async def test_moderate_comment_schema_default_action() -> None:
    """Spot-check: moderate_comment exposes an 'action' with a default."""
    server = create_server()
    tool = next(t for t in await server.list_tools() if t.name == "moderate_comment")
    props = tool.inputSchema["properties"]
    assert "action" in props
    assert props["action"].get("default") == "published"


def test_register_all_tools_is_idempotent() -> None:
    """Registering all tools twice does not raise and does not duplicate."""
    server = _Server("idempotency-probe")
    register_all_tools(server)
    # Second call must not raise even though tools already exist.
    register_all_tools(server)

    tools = asyncio.run(server.list_tools())
    assert len(tools) == 20
    assert len({t.name for t in tools}) == 20


def test_register_all_tools_logs_warning_on_duplicate(caplog: pytest.LogCaptureFixture) -> None:
    """Re-registration warns about existing tools but stays non-fatal."""
    server = _Server("warn-probe")
    with caplog.at_level(logging.WARNING):
        register_all_tools(server)
        register_all_tools(server)
    # The SDK's tool manager emits "Tool already exists" warnings.
    assert any("already exists" in rec.message for rec in caplog.records)
