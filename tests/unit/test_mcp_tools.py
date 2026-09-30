"""Unit tests for the MCP tool modules (``ytmcp.mcp.tools.*``).

Tools are thin async wrappers around the core services. Rather than mocking
HTTP for every call, this module monkeypatches the *service methods* that the
tools invoke. That keeps every test fast and network-free while still asserting
the real contract: the tools forward their parameters, serialise results to a
valid JSON string, and never let an unawaited client leak.

``open_client`` is also exercised directly (both authenticated and anonymous
paths, plus the always-close guarantee and ``text_result``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from ytmcp.config import get_settings
from ytmcp.core import (
    AnalyticsService,
    ChannelService,
    CommentService,
    MetadataService,
    PlaylistService,
    SearchService,
    UploadController,
)
from ytmcp.core.models import (
    Analytics,
    Channel,
    Comment,
    Playlist,
    UploadResult,
    Video,
    VideoStatus,
    Visibility,
)
from ytmcp.mcp.tools import (
    analytics_tools,
    comment_tools,
    playlist_tools,
    video_tools,
)
from ytmcp.mcp.tools._helpers import open_client, text_result

# ``asyncio_mode = "auto"`` in pyproject auto-detects ``async def`` tests, so no
# module-level asyncio marker is needed (which would wrongly flag sync tests).

# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Pin ``Settings`` to an isolated data dir (mirrors ``test_cli``)."""
    for var in (
        "YTMCP_DATA_DIR",
        "YTMCP_COOKIE_FILE",
        "YTMCP_OAUTH_CLIENT_SECRET",
        "YTMCP_OAUTH_REFRESH_TOKEN",
        "YTMCP_OAUTH_CLIENT_ID",
        "YTMCP_AUTH_MODE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("YTMCP_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def no_auth(monkeypatch: pytest.MonkeyPatch):
    """Neutralise ``AuthResolver.apply`` so authenticated tools don't need creds.

    The tools call ``open_client(authenticated=True)`` which would otherwise
    raise ``AuthRequiredError`` because the test settings carry no credentials.
    """

    async def _fake_apply(self: Any, client: Any) -> Any:  # noqa: ANN401
        return None

    monkeypatch.setattr("ytmcp.mcp.tools._helpers.AuthResolver.apply", _fake_apply)


def _json(text: str) -> Any:
    """Parse a tool result, asserting it is well-formed JSON first."""
    return json.loads(text)


def _sample_video(video_id: str = "abc123") -> Video:
    return Video(video_id=video_id, title="T", url=f"https://www.youtube.com/watch?v={video_id}")


def _sample_playlist(playlist_id: str = "PL123") -> Playlist:
    return Playlist(playlist_id=playlist_id, title="PL")


def _sample_comment(comment_id: str = "c1") -> Comment:
    return Comment(comment_id=comment_id, text="hi")


def _sample_upload_result() -> UploadResult:
    return UploadResult(
        video_id="vid-1",
        title="My video",
        status=VideoStatus(upload_status="uploaded", privacy_status=Visibility.PRIVATE),
        url="https://www.youtube.com/watch?v=vid-1",
    )


# --------------------------------------------------------------------------- #
# _helpers.open_client
# --------------------------------------------------------------------------- #
async def test_open_client_anonymous_does_not_apply_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[Any] = []

    async def _fake_apply(self: Any, client: Any) -> Any:  # noqa: ANN401
        applied.append(client)
        return None

    monkeypatch.setattr("ytmcp.mcp.tools._helpers.AuthResolver.apply", _fake_apply)

    async with open_client() as client:
        assert client is not None
        # Anonymous path never resolves auth.
        assert applied == []


async def test_open_client_authenticated_applies_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[Any] = []

    async def _fake_apply(self: Any, client: Any) -> Any:  # noqa: ANN401
        applied.append(client)
        return None

    monkeypatch.setattr("ytmcp.mcp.tools._helpers.AuthResolver.apply", _fake_apply)

    async with open_client(authenticated=True) as client:
        assert applied and applied[0] is client


async def test_open_client_always_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    async def _fake_close(self: Any) -> None:  # noqa: ANN401
        closed.append(True)

    monkeypatch.setattr("ytmcp.mcp.tools._helpers.YouTubeClient.close", _fake_close)

    async with open_client():
        assert closed == []
    assert closed == [True]


async def test_open_client_closes_even_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    async def _fake_close(self: Any) -> None:  # noqa: ANN401
        closed.append(True)

    monkeypatch.setattr("ytmcp.mcp.tools._helpers.YouTubeClient.close", _fake_close)

    with pytest.raises(RuntimeError):
        async with open_client():
            raise RuntimeError("boom")
    assert closed == [True]


async def test_open_client_accepts_settings_override(settings: Any) -> None:
    async with open_client(settings=settings) as client:
        assert client.settings is settings


def test_text_result_returns_message() -> None:
    assert text_result("hello") == "hello"
    assert text_result("") == ""


# --------------------------------------------------------------------------- #
# video_tools
# --------------------------------------------------------------------------- #
async def test_upload_video_serialises_result_and_forwards_params(
    monkeypatch: pytest.MonkeyPatch, no_auth: None, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_upload(self: Any, file_path: Any, **kwargs: Any) -> UploadResult:  # noqa: ANN401
        captured["file_path"] = file_path
        captured.update(kwargs)
        return _sample_upload_result()

    monkeypatch.setattr(UploadController, "upload", _fake_upload)

    out = await video_tools.upload_video(
        str(tmp_path / "v.mp4"),
        title="My video",
        description="desc",
        tags=["a", "b"],
        visibility="public",
        category_id="10",
        made_for_kids=True,
        thumbnail_path=str(tmp_path / "t.png"),
    )

    data = _json(out)
    assert data["video_id"] == "vid-1"
    assert data["url"] == "https://www.youtube.com/watch?v=vid-1"
    # Parameters were forwarded, and visibility was coerced to the enum.
    assert captured["title"] == "My video"
    assert captured["description"] == "desc"
    assert captured["tags"] == ["a", "b"]
    assert captured["category_id"] == "10"
    assert captured["made_for_kids"] is True
    assert captured["visibility"] is Visibility.PUBLIC
    assert captured["file_path"].endswith("v.mp4")


async def test_upload_video_visibility_upper_is_parsed(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    seen: dict[str, Any] = {}

    async def _fake_upload(self: Any, file_path: Any, **kwargs: Any) -> UploadResult:  # noqa: ANN401
        seen["visibility"] = kwargs["visibility"]
        return _sample_upload_result()

    monkeypatch.setattr(UploadController, "upload", _fake_upload)
    await video_tools.upload_video("f.mp4", title="t", visibility="unlisted")
    assert seen["visibility"] is Visibility.UNLISTED


async def test_upload_video_rejects_invalid_visibility(no_auth: None) -> None:
    with pytest.raises(ValueError):
        await video_tools.upload_video("f.mp4", title="t", visibility="nope")


async def test_update_video_metadata_serialises_and_forwards(
    monkeypatch: pytest.MonkeyPatch, no_auth: None
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_update(self: Any, video_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        captured["video_id"] = video_id
        captured.update(kwargs)
        return {"video_id": video_id, "updated_fields": ["title"]}

    monkeypatch.setattr(MetadataService, "update_metadata", _fake_update)

    out = await video_tools.update_video_metadata(
        "vid-1", title="New", description="D", tags=["x"], category_id="22", default_language="en"
    )
    data = _json(out)
    assert data["video_id"] == "vid-1"
    assert captured == {
        "video_id": "vid-1",
        "title": "New",
        "description": "D",
        "tags": ["x"],
        "category_id": "22",
        "default_language": "en",
    }


async def test_set_video_visibility_serialises(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    seen: dict[str, Any] = {}

    async def _fake_set(self: Any, video_id: str, visibility: Visibility, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        seen["video_id"] = video_id
        seen["visibility"] = visibility
        seen.update(kwargs)
        return {"video_id": video_id, "visibility": visibility.value}

    monkeypatch.setattr(MetadataService, "set_visibility", _fake_set)

    out = await video_tools.set_video_visibility("vid-1", "public", publish_at="2026-01-01T00:00:00Z")
    data = _json(out)
    assert data["visibility"] == "PUBLIC"
    assert seen["visibility"] is Visibility.PUBLIC
    assert seen["publish_at"] == "2026-01-01T00:00:00Z"


async def test_delete_video_returns_plain_string(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    async def _fake_delete(self: Any, video_id: str) -> bool:  # noqa: ANN401
        return True

    monkeypatch.setattr(MetadataService, "delete_video", _fake_delete)
    out = await video_tools.delete_video("vid-1")
    assert out == "Deleted video vid-1: True"


async def test_get_video_info_serialises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_get(self: Any, video_id: str) -> Video:  # noqa: ANN401
        return _sample_video(video_id)

    monkeypatch.setattr(MetadataService, "get_video", _fake_get)
    data = _json(await video_tools.get_video_info("abc123"))
    assert data["video_id"] == "abc123"
    assert data["url"].endswith("abc123")


async def test_search_videos_serialises_list(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake_search(self: Any, query: str, *, limit: int = 20) -> list[Video]:  # noqa: ANN401
        seen["query"] = query
        seen["limit"] = limit
        return [_sample_video("a"), _sample_video("b")]

    monkeypatch.setattr(SearchService, "search", _fake_search)
    data = _json(await video_tools.search_videos("cats", limit=2))
    assert [v["video_id"] for v in data] == ["a", "b"]
    assert seen == {"query": "cats", "limit": 2}


async def test_list_channel_videos_serialises_list(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake_list(self: Any, channel_id: str, *, limit: int = 30) -> list[Video]:  # noqa: ANN401
        seen["channel_id"] = channel_id
        seen["limit"] = limit
        return [_sample_video("c")]

    monkeypatch.setattr(SearchService, "list_channel_videos", _fake_list)
    data = _json(await video_tools.list_channel_videos("UC1", limit=5))
    assert data[0]["video_id"] == "c"
    assert seen == {"channel_id": "UC1", "limit": 5}


# --------------------------------------------------------------------------- #
# playlist_tools
# --------------------------------------------------------------------------- #
async def test_create_playlist_forwards_and_serialises(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    captured: dict[str, Any] = {}

    async def _fake_create(self: Any, title: str, **kwargs: Any) -> Playlist:  # noqa: ANN401
        captured["title"] = title
        captured.update(kwargs)
        return _sample_playlist()

    monkeypatch.setattr(PlaylistService, "create", _fake_create)
    data = _json(await playlist_tools.create_playlist("PL", description="d", visibility="public"))
    assert data["playlist_id"] == "PL123"
    assert captured["title"] == "PL"
    assert captured["description"] == "d"
    assert captured["visibility"] is Visibility.PUBLIC


async def test_list_playlists_serialises_list(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake_list(self: Any, channel_id: str) -> list[Playlist]:  # noqa: ANN401
        seen["channel_id"] = channel_id
        return [_sample_playlist("PL1"), _sample_playlist("PL2")]

    monkeypatch.setattr(PlaylistService, "list_playlists", _fake_list)
    data = _json(await playlist_tools.list_playlists("UC1"))
    assert [p["playlist_id"] for p in data] == ["PL1", "PL2"]
    assert seen == {"channel_id": "UC1"}


async def test_add_video_to_playlist_plain_string(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    seen: dict[str, Any] = {}

    async def _fake_add(self: Any, playlist_id: str, video_id: str) -> bool:  # noqa: ANN401
        seen.update(playlist_id=playlist_id, video_id=video_id)
        return True

    monkeypatch.setattr(PlaylistService, "add_video", _fake_add)
    out = await playlist_tools.add_video_to_playlist("PL1", "vid1")
    assert out == "Added vid1 to PL1: True"
    assert seen == {"playlist_id": "PL1", "video_id": "vid1"}


async def test_remove_video_from_playlist_plain_string(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    async def _fake_remove(self: Any, playlist_id: str, video_id: str) -> bool:  # noqa: ANN401
        return False

    monkeypatch.setattr(PlaylistService, "remove_video", _fake_remove)
    out = await playlist_tools.remove_video_from_playlist("PL1", "vid1")
    assert out == "Removed vid1 from PL1: False"


async def test_delete_playlist_plain_string(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    async def _fake_delete(self: Any, playlist_id: str) -> bool:  # noqa: ANN401
        return True

    monkeypatch.setattr(PlaylistService, "delete", _fake_delete)
    assert await playlist_tools.delete_playlist("PL1") == "Deleted playlist PL1: True"


# --------------------------------------------------------------------------- #
# comment_tools
# --------------------------------------------------------------------------- #
async def test_list_comments_serialises_list(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake_list(self: Any, video_id: str, *, limit: int = 20) -> list[Comment]:  # noqa: ANN401
        seen["video_id"] = video_id
        seen["limit"] = limit
        return [_sample_comment("c1"), _sample_comment("c2")]

    monkeypatch.setattr(CommentService, "list_comments", _fake_list)
    data = _json(await comment_tools.list_comments("vid1", limit=2))
    assert [c["comment_id"] for c in data] == ["c1", "c2"]
    assert seen == {"video_id": "vid1", "limit": 2}


async def test_reply_to_comment_serialises(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    seen: dict[str, Any] = {}

    async def _fake_reply(self: Any, video_id: str, comment_id: str, text: str) -> Comment:  # noqa: ANN401
        seen.update(video_id=video_id, comment_id=comment_id, text=text)
        return Comment(comment_id="pending", video_id=video_id, text=text, is_reply=True)

    monkeypatch.setattr(CommentService, "reply", _fake_reply)
    data = _json(await comment_tools.reply_to_comment("vid1", "c1", "hello"))
    assert data["is_reply"] is True
    assert data["text"] == "hello"
    assert seen == {"video_id": "vid1", "comment_id": "c1", "text": "hello"}


@pytest.mark.parametrize("action", ["published", "heldForReview", "rejected"])
async def test_moderate_comment_plain_string(monkeypatch: pytest.MonkeyPatch, no_auth: None, action: str) -> None:
    seen: dict[str, Any] = {}

    async def _fake_moderate(self: Any, comment_id: str, act: str = "published") -> bool:  # noqa: ANN401
        seen.update(comment_id=comment_id, action=act)
        return True

    monkeypatch.setattr(CommentService, "moderate", _fake_moderate)
    out = await comment_tools.moderate_comment("c1", action)
    assert out == f"Comment c1 set to '{action}': True"
    assert seen == {"comment_id": "c1", "action": action}


async def test_moderate_comment_defaults_to_published(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    async def _fake_moderate(self: Any, comment_id: str, act: str = "published") -> bool:  # noqa: ANN401
        assert act == "published"
        return True

    monkeypatch.setattr(CommentService, "moderate", _fake_moderate)
    assert "published" in await comment_tools.moderate_comment("c9")


# --------------------------------------------------------------------------- #
# analytics_tools
# --------------------------------------------------------------------------- #
async def test_get_channel_info_serialises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_info(self: Any, channel_id: str | None = None) -> Channel:  # noqa: ANN401
        return Channel(channel_id=channel_id or "own", title="Chan")

    monkeypatch.setattr(ChannelService, "get_info", _fake_info)
    data = _json(await analytics_tools.get_channel_info("UC1"))
    assert data["channel_id"] == "UC1"
    assert data["title"] == "Chan"


async def test_get_channel_info_anonymous_when_no_id(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    seen: dict[str, Any] = {}

    async def _fake_info(self: Any, channel_id: str | None = None) -> Channel:  # noqa: ANN401
        seen["channel_id"] = channel_id
        return Channel(channel_id="own")

    monkeypatch.setattr(ChannelService, "get_info", _fake_info)
    await analytics_tools.get_channel_info()
    assert seen["channel_id"] is None


async def test_get_channel_info_authenticated_when_no_id(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[Any] = []

    async def _fake_apply(self: Any, client: Any) -> Any:  # noqa: ANN401
        applied.append(client)
        return None

    async def _fake_info(self: Any, channel_id: str | None = None) -> Channel:  # noqa: ANN401
        return Channel(channel_id="own")

    monkeypatch.setattr("ytmcp.mcp.tools._helpers.AuthResolver.apply", _fake_apply)
    monkeypatch.setattr(ChannelService, "get_info", _fake_info)
    await analytics_tools.get_channel_info(None)
    assert applied  # auth applied because channel_id is None


async def test_get_subscriber_count_known(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_count(self: Any, channel_id: str | None = None) -> int | None:  # noqa: ANN401
        return 42

    monkeypatch.setattr(ChannelService, "get_subscriber_count", _fake_count)
    assert await analytics_tools.get_subscriber_count("UC1") == "Subscribers: 42"


async def test_get_subscriber_count_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_count(self: Any, channel_id: str | None = None) -> int | None:  # noqa: ANN401
        return None

    monkeypatch.setattr(ChannelService, "get_subscriber_count", _fake_count)
    assert await analytics_tools.get_subscriber_count("UC1") == "Subscribers: unknown"


async def test_update_channel_branding_forwards(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    seen: dict[str, Any] = {}

    async def _fake_update(self: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        seen.update(kwargs)
        return {"banner": {"status": "staged"}}

    monkeypatch.setattr(ChannelService, "update_branding", _fake_update)
    links = [{"title": "site", "url": "https://x"}]
    data = _json(await analytics_tools.update_channel_branding(banner_path="b.png", links=links))
    assert data == {"banner": {"status": "staged"}}
    assert seen == {"banner_path": "b.png", "avatar_path": None, "links": links}


async def test_get_analytics_serialises(monkeypatch: pytest.MonkeyPatch, no_auth: None) -> None:
    seen: dict[str, Any] = {}

    async def _fake_analytics(self: Any, channel_id: str, *, period_days: int = 28) -> Analytics:  # noqa: ANN401
        seen.update(channel_id=channel_id, period_days=period_days)
        return Analytics(channel_id=channel_id, period_days=period_days, views=100)

    monkeypatch.setattr(AnalyticsService, "get_channel_analytics", _fake_analytics)
    data = _json(await analytics_tools.get_analytics("UC1", period_days=7))
    assert data["views"] == 100
    assert seen == {"channel_id": "UC1", "period_days": 7}


async def test_get_top_videos_serialises_list(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake_top(self: Any, channel_id: str, *, limit: int = 10) -> list[Video]:  # noqa: ANN401
        seen.update(channel_id=channel_id, limit=limit)
        return [_sample_video("t1")]

    monkeypatch.setattr(AnalyticsService, "get_top_videos", _fake_top)
    data = _json(await analytics_tools.get_top_videos("UC1", limit=3))
    assert data[0]["video_id"] == "t1"
    assert seen == {"channel_id": "UC1", "limit": 3}


# --------------------------------------------------------------------------- #
# register() on a fresh FastMCP server
# --------------------------------------------------------------------------- #
def test_register_each_module_on_fresh_server() -> None:
    from mcp.server import FastMCP

    for module, expected in (
        (video_tools, 7),
        (playlist_tools, 5),
        (comment_tools, 3),
        (analytics_tools, 5),
    ):

        async def _run(mod: Any = module) -> list[Any]:  # noqa: ANN401
            server = FastMCP("test")
            mod.register(server)
            return await server.list_tools()

        tools = asyncio.run(_run())
        assert len(tools) == expected, f"{module.__name__} registered {len(tools)} tools"


def test_all_modules_register_without_error_on_shared_server() -> None:
    from mcp.server import FastMCP

    server = FastMCP("shared")
    video_tools.register(server)
    playlist_tools.register(server)
    comment_tools.register(server)
    analytics_tools.register(server)

    tools = asyncio.run(server.list_tools())
    assert len(tools) == 20


def test_register_uses_tool_decorator() -> None:
    """``register`` must call ``mcp.tool()`` once per tool function."""

    class _Recorder:
        def __init__(self) -> None:
            self.names: list[str] = []

        def tool(self):
            def _wrap(fn):  # noqa: ANN001
                self.names.append(fn.__name__)
                return fn

            return _wrap

    rec = _Recorder()
    video_tools.register(rec)
    assert rec.names == [
        "upload_video",
        "update_video_metadata",
        "set_video_visibility",
        "delete_video",
        "get_video_info",
        "search_videos",
        "list_channel_videos",
    ]


def test_open_client_logs_nothing_when_unexpected(caplog: pytest.LogCaptureFixture) -> None:
    """Sanity: importing the helpers does not emit module-level log noise."""
    with caplog.at_level(logging.WARNING):
        import importlib

        importlib.reload(analytics_tools)
    assert isinstance(caplog.records, list)
