"""Tests for the MCP server and the tool registry.

Covers:
- ``create_server`` builds a server without error.
- exactly 20 tools are exposed, with the expected names.
- every tool has a non-empty description and a valid object input schema.
- ``register_all_tools`` is effectively idempotent (re-registering does not
  error and does not duplicate tools).
- adversarial probes: no orphan/extra tools, schema fallbacks, deterministic
  ordering-independent results.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from ytmcp.config import Settings
from ytmcp.mcp.server import _Server, create_server, main
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

EXPECTED_TOOL_COUNT = 20


async def _list_tools() -> list:
    """Build a server and return its tools (helper for async tests)."""
    return await create_server().list_tools()


def _schema_of(tool: object) -> dict:
    """Return a tool's input schema, tolerating SDK attribute naming.

    The SDK has exposed both ``inputSchema`` and ``input_schema`` across
    versions; accept either so the test is version-robust.
    """
    schema = getattr(tool, "inputSchema", None)
    if schema is None:
        schema = getattr(tool, "input_schema", None)
    assert isinstance(schema, dict), f"{getattr(tool, 'name', tool)} has no schema dict"
    return schema


# --------------------------------------------------------------------------- #
# Basic construction & surface
# --------------------------------------------------------------------------- #
async def test_create_server_returns_server() -> None:
    """create_server() builds a server object without raising."""
    server = create_server()
    assert server is not None
    tools = await server.list_tools()
    assert isinstance(tools, list)


async def test_list_tools_returns_exactly_20() -> None:
    """The server exposes exactly 20 tools."""
    tools = await _list_tools()
    assert len(tools) == EXPECTED_TOOL_COUNT


async def test_tool_names_match_expected_set() -> None:
    """The set of tool names matches the expected surface exactly."""
    names = {t.name for t in await _list_tools()}
    assert names == EXPECTED_TOOLS


async def test_tool_names_no_duplicates() -> None:
    """No duplicate tool names are registered."""
    names = [t.name for t in await _list_tools()]
    assert len(names) == len(set(names))


async def test_all_expected_names_present_individually() -> None:
    """Adversarial: every single expected tool is present (no silent drop)."""
    names = {t.name for t in await _list_tools()}
    missing = EXPECTED_TOOLS - names
    assert not missing, f"missing tools: {sorted(missing)}"


async def test_no_orphan_tools_beyond_expected() -> None:
    """Adversarial: no unexpected/extra tools leaked into the surface."""
    names = {t.name for t in await _list_tools()}
    extra = names - EXPECTED_TOOLS
    assert not extra, f"unexpected tools: {sorted(extra)}"


# --------------------------------------------------------------------------- #
# Descriptions & schemas
# --------------------------------------------------------------------------- #
async def test_every_tool_has_non_empty_description() -> None:
    """Each tool exposes a non-empty description."""
    for tool in await _list_tools():
        assert tool.description, f"{tool.name} has no description"
        assert tool.description.strip(), f"{tool.name} description is blank"


async def test_every_tool_has_object_input_schema() -> None:
    """Each tool has an object input schema with a properties mapping."""
    for tool in await _list_tools():
        schema = _schema_of(tool)
        assert schema.get("type") == "object", f"{tool.name} schema type != object"
        props = schema.get("properties")
        assert isinstance(props, dict), f"{tool.name} has no properties mapping"
        assert props, f"{tool.name} has empty properties"


async def test_upload_video_schema_exposes_expected_params() -> None:
    """Spot-check: upload_video declares its key parameters."""
    tool = next(t for t in await _list_tools() if t.name == "upload_video")
    props = _schema_of(tool)["properties"]
    for param in ("file_path", "title", "description", "visibility"):
        assert param in props, f"upload_video missing param '{param}'"
    required = _schema_of(tool).get("required", [])
    assert "file_path" in required
    assert "title" in required


async def test_moderate_comment_schema_default_action() -> None:
    """Spot-check: moderate_comment exposes an 'action' with a default."""
    tool = next(t for t in await _list_tools() if t.name == "moderate_comment")
    props = _schema_of(tool)["properties"]
    assert "action" in props
    assert props["action"].get("default") == "published"


async def test_schema_param_counts_are_stable() -> None:
    """Adversarial: upload_video has the richest schema (>= 9 params)."""
    tools = {t.name: t for t in await _list_tools()}
    upload_props = _schema_of(tools["upload_video"])["properties"]
    assert len(upload_props) >= 9
    for name, tool in tools.items():
        if name != "upload_video":
            assert len(_schema_of(tool)["properties"]) <= len(upload_props)


# --------------------------------------------------------------------------- #
# Idempotency of registration
# --------------------------------------------------------------------------- #
def test_register_all_tools_is_idempotent() -> None:
    """Registering all tools twice does not raise and does not duplicate."""
    server = _Server("idempotency-probe")
    register_all_tools(server)
    # Second call must not raise even though tools already exist.
    register_all_tools(server)

    tools = asyncio.run(server.list_tools())
    assert len(tools) == EXPECTED_TOOL_COUNT
    assert len({t.name for t in tools}) == EXPECTED_TOOL_COUNT


def test_register_all_tools_logs_warning_on_duplicate(caplog: pytest.LogCaptureFixture) -> None:
    """Re-registration warns about existing tools but stays non-fatal."""
    server = _Server("warn-probe")
    with caplog.at_level(logging.WARNING):
        register_all_tools(server)
        register_all_tools(server)
    # The SDK's tool manager emits "Tool already exists" warnings.
    assert any("already exists" in rec.message for rec in caplog.records)


def test_register_all_tools_on_fresh_server_matches_create_server() -> None:
    """Adversarial: manual registration yields the same surface as the factory."""
    manual = _Server("manual-probe")
    register_all_tools(manual)
    manual_names = {t.name for t in asyncio.run(manual.list_tools())}
    assert manual_names == EXPECTED_TOOLS


# --------------------------------------------------------------------------- #
# main(): transport selection
# --------------------------------------------------------------------------- #
class _DummyServer:
    """A dummy ``mcp`` server recording the transport passed to ``run``."""

    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> None:
        self.run_calls.append(kwargs)

    @property
    def transport(self) -> str | None:
        return self.run_calls[-1].get("transport") if self.run_calls else None


def _settings_with_transport(transport: str) -> Settings:
    """Build a Settings instance carrying an arbitrary transport string.

    ``Settings.mcp_transport`` is a ``Literal["stdio", "http"]``, so transports
    such as ``"sse"`` or an unknown value cannot pass normal validation. Use
    ``model_construct`` to bypass validation and exercise the fallback logic in
    ``main()``.
    """
    return Settings.model_construct(mcp_transport=transport, log_level="INFO")


def _patch_main(monkeypatch: pytest.MonkeyPatch, transport: str) -> _DummyServer:
    """Patch ``create_server`` + ``get_settings`` used by ``main()``.

    Returns the dummy server so callers can assert on the ``run`` invocation.
    """
    dummy = _DummyServer()
    created: list[bool] = []

    def _fake_create_server() -> _DummyServer:
        created.append(True)
        return dummy

    monkeypatch.setattr("ytmcp.mcp.server.create_server", _fake_create_server)
    monkeypatch.setattr(
        "ytmcp.mcp.server.get_settings", lambda: _settings_with_transport(transport)
    )
    dummy.created = created  # type: ignore[attr-defined]
    return dummy


def test_main_stdio_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """mcp_transport='stdio' runs the server over stdio."""
    dummy = _patch_main(monkeypatch, "stdio")
    main()
    assert dummy.run_calls == [{"transport": "stdio"}]


def test_main_http_transport_maps_to_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """mcp_transport='http' is mapped to the SDK's 'streamable-http'."""
    dummy = _patch_main(monkeypatch, "http")
    main()
    assert dummy.transport == "streamable-http"


def test_main_sse_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """mcp_transport='sse' is passed through unchanged."""
    dummy = _patch_main(monkeypatch, "sse")
    main()
    assert dummy.transport == "sse"


def test_main_unknown_transport_falls_back_to_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognised transport falls back to 'stdio'."""
    dummy = _patch_main(monkeypatch, "xyz")
    main()
    assert dummy.transport == "stdio"


def test_main_calls_create_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() builds the server via create_server()."""
    dummy = _patch_main(monkeypatch, "stdio")
    main()
    assert dummy.created == [True]  # type: ignore[attr-defined]
    assert len(dummy.run_calls) == 1


def test_main_runs_exactly_once_with_transport_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adversarial: run() is invoked exactly once and only with a transport kwarg."""
    dummy = _patch_main(monkeypatch, "http")
    main()
    assert len(dummy.run_calls) == 1
    assert set(dummy.run_calls[0]) == {"transport"}


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("stdio", "stdio"),
        ("http", "streamable-http"),
        ("sse", "sse"),
        ("xyz", "stdio"),
        ("", "stdio"),
        ("HTTP", "stdio"),  # case-sensitive: unknown -> fallback
    ],
)
def test_main_transport_matrix(
    monkeypatch: pytest.MonkeyPatch, configured: str, expected: str
) -> None:
    """Full transport mapping table for main()."""
    dummy = _patch_main(monkeypatch, configured)
    main()
    assert dummy.transport == expected
