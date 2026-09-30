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
@respx.mock
async def test_remove_video_raises_on_4xx(settings: Settings) -> None:
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
