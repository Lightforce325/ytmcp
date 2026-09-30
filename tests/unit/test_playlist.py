"""Tests for core/playlist.py: parsers and PlaylistService endpoints."""

from __future__ import annotations

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core.client import YouTubeClient
from ytmcp.core.exceptions import UpstreamError
from ytmcp.core.models import Visibility
from ytmcp.core.playlist import (
    PLAYLIST_MANAGER,
    PlaylistService,
    _parse_playlists,
    _safe_json,
    _walk_playlists,
)


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #
def test_parse_playlists_from_regex() -> None:
    html = (
        '"playlistId":"PLabc1234567890123456789012","x":{"title":{"runs":[{"text":"My Playlist"}]}'
    )
    playlists = _parse_playlists(html)
    assert len(playlists) == 1
    assert playlists[0].playlist_id == "PLabc1234567890123456789012"
    assert playlists[0].title == "My Playlist"


def test_parse_playlists_from_ytinitialdata() -> None:
    html = (
        "<script>var ytInitialData = "
        '{"contents":{"a":{"lockupViewModel":{"contentId":"PLxyz9999999999999999999999",'
        '"metadata":{"lockupMetadataViewModel":{"title":{"content":"Fav Songs"}}}}}}};</script>'
    )
    playlists = _parse_playlists(html)
    assert len(playlists) == 1
    assert playlists[0].playlist_id == "PLxyz9999999999999999999999"
    assert playlists[0].title == "Fav Songs"


def test_parse_playlists_ignores_non_pl_content_ids() -> None:
    node = {
        "lockupViewModel": {
            "contentId": "VIDEO123",
            "metadata": {"lockupMetadataViewModel": {"title": {"content": "not a playlist"}}},
        }
    }
    assert _walk_playlists(node) == []


def test_parse_playlists_empty_html() -> None:
    assert _parse_playlists("<html></html>") == []

def test_parse_playlists_broken_json_returns_empty() -> None:
    """Malformed ``ytInitialData`` JSON must be swallowed (lines 113-114).

    The ``except json.JSONDecodeError: pass`` branch returns an empty list
    instead of raising, and no regex hits are expected either.
    """
    html = (
        "<script>var ytInitialData = "
        '{"contents": {"a": <not json here> }};</script>'
    )
    assert _parse_playlists(html) == []

def test_parse_playlists_broken_json_does_not_raise() -> None:
    """Guard: a truncated JSON object must never propagate ``JSONDecodeError``."""
    html = '<script>var ytInitialData = {"contents": [1, 2, };</script>'
    result = _parse_playlists(html)
    assert result == []

def test_walk_playlists_nested_lists() -> None:
    """``elif isinstance(node, list)`` (lines 132-133) + recursion into lists.

    A list-of-lists-of-dicts must surface every ``lockupViewModel`` playlist.
    """
    node = [
        [
            {
                "lockupViewModel": {
                    "contentId": "PLaaaaaaaaaaaaaaaaaaaaaa",
                    "metadata": {
                        "lockupMetadataViewModel": {"title": {"content": "First"}}
                    },
                }
            }
        ],
        {
            "lockupViewModel": {
                "contentId": "PLbbbbbbbbbbbbbbbbbbbbbb",
                "metadata": {"lockupMetadataViewModel": {"title": {"content": "Second"}}},
            }
        },
        "not-a-node",
        42,
    ]
    found = _walk_playlists(node)
    ids = [p.playlist_id for p in found]
    assert ids == ["PLaaaaaaaaaaaaaaaaaaaaaa", "PLbbbbbbbbbbbbbbbbbbbbbb"]
    titles = {p.playlist_id: p.title for p in found}
    assert titles["PLaaaaaaaaaaaaaaaaaaaaaa"] == "First"
    assert titles["PLbbbbbbbbbbbbbbbbbbbbbb"] == "Second"

def test_walk_playlists_deep_list_recursion() -> None:
    """Recursion through arbitrarily nested lists of dicts."""
    inner = {
        "lockupViewModel": {
            "contentId": "PLcccccccccccccccccccccc",
            "metadata": {"lockupMetadataViewModel": {"title": {"content": "Deep"}}},
        }
    }
    node = {"a": [{"b": [{"c": [inner]}]}]}
    found = _walk_playlists(node)
    assert [p.playlist_id for p in found] == ["PLcccccccccccccccccccccc"]
    assert found[0].title == "Deep"

def test_walk_playlists_ignores_scalars() -> None:
    assert _walk_playlists("just a string") == []
    assert _walk_playlists(123) == []
    assert _walk_playlists(None) == []

def test_parse_playlists_falls_back_to_broken_ytinitialdata() -> None:
    """End-to-end: regex misses, ytInitialData is corrupt -> empty list."""
    html = (
        "<html><script>var ytInitialData = "
        '{"contents": {"x": oops}};</script></html>'
    )
    assert _parse_playlists(html) == []


def test_walk_playlists_missing_title() -> None:
    node = {"lockupViewModel": {"contentId": "PLabcdefghij0123456789"}}
    found = _walk_playlists(node)
    assert len(found) == 1
    assert found[0].title == ""


def test_safe_json_invalid_body() -> None:
    resp = httpx.Response(200, text="not json")
    assert _safe_json(resp) == {}


def test_safe_json_valid_body() -> None:
    resp = httpx.Response(200, json={"playlistId": "PL1"})
    assert _safe_json(resp) == {"playlistId": "PL1"}


# --------------------------------------------------------------------------- #
# PlaylistService.create
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_create_returns_playlist(settings: Settings) -> None:
    route = respx.post(PLAYLIST_MANAGER).mock(
        return_value=httpx.Response(200, json={"playlistId": "PLnew123456789012345678901"})
    )
    async with YouTubeClient(settings) as client:
        pl = await PlaylistService(client).create(
            "Judul", description="desc", visibility=Visibility.PUBLIC
        )
    assert pl.playlist_id == "PLnew123456789012345678901"
    assert pl.title == "Judul"
    assert pl.visibility is Visibility.PUBLIC
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "Judul" in body
    assert "PUBLIC" in body


@pytest.mark.asyncio
@respx.mock
async def test_create_falls_back_to_unknown_id(settings: Settings) -> None:
    respx.post(PLAYLIST_MANAGER).mock(
        return_value=httpx.Response(200, text="not json")
    )
    async with YouTubeClient(settings) as client:
        pl = await PlaylistService(client).create("X")
    assert pl.playlist_id == "unknown"


@pytest.mark.asyncio
@respx.mock
async def test_create_raises_on_4xx(settings: Settings) -> None:
    respx.post(PLAYLIST_MANAGER).mock(return_value=httpx.Response(400, text="bad"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await PlaylistService(client).create("X")


# --------------------------------------------------------------------------- #
# PlaylistService.list_playlists
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_list_playlists_parses_html(settings: Settings) -> None:
    html = '"playlistId":"PLaaaaaaaaaaaaaaaaaaaaaa","z":{"title":{"runs":[{"text":"Listed"}]}'
    respx.get("https://www.youtube.com/channel/UCchan/playlists").mock(
        return_value=httpx.Response(200, text=html)
    )
    async with YouTubeClient(settings) as client:
        playlists = await PlaylistService(client).list_playlists("UCchan")
    assert len(playlists) == 1
    assert playlists[0].title == "Listed"


@pytest.mark.asyncio
@respx.mock
async def test_list_playlists_raises_on_4xx(settings: Settings) -> None:
    respx.get("https://www.youtube.com/channel/UCchan/playlists").mock(
        return_value=httpx.Response(404)
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await PlaylistService(client).list_playlists("UCchan")


# --------------------------------------------------------------------------- #
# add / remove / delete
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_add_video_payload(settings: Settings) -> None:
    route = respx.post(f"{PLAYLIST_MANAGER.rsplit('/', 1)[0]}/playlist_manager/add").mock(
        return_value=httpx.Response(200, json={})
    )
    async with YouTubeClient(settings) as client:
        ok = await PlaylistService(client).add_video("PL1", "VID1")
    assert ok is True
    body = route.calls.last.request.content.decode()
    assert "PL1" in body
    assert "VID1" in body


@pytest.mark.asyncio
@respx.mock
async def test_add_video_raises_on_4xx(settings: Settings) -> None:
    respx.post(f"{PLAYLIST_MANAGER.rsplit('/', 1)[0]}/playlist_manager/add").mock(
        return_value=httpx.Response(403)
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await PlaylistService(client).add_video("PL1", "VID1")


@pytest.mark.asyncio
@respx.mock
async def test_remove_video_ok(settings: Settings) -> None:
    route = respx.post(f"{PLAYLIST_MANAGER.rsplit('/', 1)[0]}/playlist_manager/remove").mock(
        return_value=httpx.Response(200, json={})
    )
    async with YouTubeClient(settings) as client:
        ok = await PlaylistService(client).remove_video("PL1", "VID1")
    assert ok is True
    assert route.called


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 404])
@respx.mock
async def test_remove_video_raises_on_4xx(settings: Settings, status: int) -> None:
    """Line 77: a client error maps to an ``UpstreamError`` with the reason.

    4xx are not retried by the client, so this path is hit immediately.
    """
    route = respx.post(f"{PLAYLIST_MANAGER.rsplit('/', 1)[0]}/playlist_manager/remove").mock(
        return_value=httpx.Response(status, text="nope")
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError) as excinfo:
            await PlaylistService(client).remove_video("PL1", "VID1")
    assert str(status) in str(excinfo.value)
    assert route.call_count == 1, "4xx must not be retried"
    body = route.calls.last.request.content.decode()
    assert "PL1" in body
    assert "VID1" in body

@pytest.mark.asyncio
@respx.mock
async def test_remove_video_error_message(settings: Settings) -> None:
    """The raised error carries the ``Remove from playlist failed`` message."""
    respx.post(f"{PLAYLIST_MANAGER.rsplit('/', 1)[0]}/playlist_manager/remove").mock(
        return_value=httpx.Response(400, text="bad request")
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError, match="Remove from playlist failed"):
            await PlaylistService(client).remove_video("PL1", "VID1")

@pytest.mark.asyncio
@respx.mock
async def test_remove_video_raises_on_5xx(settings: Settings) -> None:
    respx.post(f"{PLAYLIST_MANAGER.rsplit('/', 1)[0]}/playlist_manager/remove").mock(
        return_value=httpx.Response(500)
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await PlaylistService(client).remove_video("PL1", "VID1")


@pytest.mark.asyncio
@respx.mock
async def test_delete_playlist_ok(settings: Settings) -> None:
    route = respx.post(f"{PLAYLIST_MANAGER}/delete").mock(
        return_value=httpx.Response(200, json={})
    )
    async with YouTubeClient(settings) as client:
        ok = await PlaylistService(client).delete("PL1")
    assert ok is True
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "PL1" in body


@pytest.mark.asyncio
@respx.mock
async def test_delete_playlist_raises_on_4xx(settings: Settings) -> None:
    respx.post(f"{PLAYLIST_MANAGER}/delete").mock(return_value=httpx.Response(400))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await PlaylistService(client).delete("PL1")
