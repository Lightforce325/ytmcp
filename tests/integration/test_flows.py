"""End-to-end integration flows (opt-in) exercising several services together.

Every test here is marked ``integration`` and is skipped unless the
``YTMCP_RUN_INTEGRATION=1`` environment variable is set. All HTTP traffic is
mocked with ``respx`` — no credentials or network access are required, so the
flows also pass once the opt-in flag is enabled.

Run with::

    uv run pytest tests/integration            # everything skipped (default)
    YTMCP_RUN_INTEGRATION=1 uv run pytest -m integration
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core.auth import AuthResolver
from ytmcp.core.channel import ChannelService
from ytmcp.core.client import YouTubeClient
from ytmcp.core.comments import CommentService
from ytmcp.core.playlist import PlaylistService
from ytmcp.core.search import SearchService
from ytmcp.core.upload import STUDIO_UPLOAD_URL, UploadController

pytestmark = pytest.mark.integration

# --------------------------------------------------------------------------- #
# Shared constants / helpers
# --------------------------------------------------------------------------- #
UPLOAD_URL = "https://upload.example.com/resumable/session-us007"

PLAYLIST_MANAGER = "https://studio.youtube.com/youtubei/v1/playlist"
PLAYLIST_ADD = "https://studio.youtube.com/youtubei/v1/playlist_manager/add"

NEXT_URL_RE = r".*youtubei/v1/next.*"

CREATED_PLAYLIST_ID = "PLcreated0123456789abcdef"
UPLOADED_VIDEO_ID = "VIDuploaded001"

def _body_json(route: respx.Route) -> dict[str, Any]:
    """Return the decoded JSON body of the last request a route received."""
    return json.loads(route.calls.last.request.content.decode("utf-8"))

def _write_cookie_file(tmp_path: Path) -> Path:
    """Write a Netscape-style cookie jar and return its path."""
    content = (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSAPISID\tcookie-sapisid\n"
        ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSID\tcookie-sid\n"
    )
    path = tmp_path / "cookies.txt"
    path.write_text(content, encoding="utf-8")
    return path

def _auth_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        cookie_file=_write_cookie_file(tmp_path),
        oauth_refresh_token=None,
        rate_limit_delay=0.0,
        max_retries=1,
    )

# --------------------------------------------------------------------------- #
# Flow 1: create playlist -> upload video -> add video to playlist
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_flow_playlist_upload_add(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full create -> upload -> add flow, verifying order and payloads."""
    # Keep the upload single-chunk and deterministic.
    from ytmcp.core import upload as upload_mod

    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", 256 * 1024)

    # Record the order in which the endpoints are hit.
    order: list[str] = []

    def _create_playlist(request: httpx.Request) -> httpx.Response:
        order.append("create_playlist")
        return httpx.Response(200, json={"playlistId": CREATED_PLAYLIST_ID})

    def _upload_init(request: httpx.Request) -> httpx.Response:
        order.append("upload_init")
        return httpx.Response(200, headers={"X-Upload-Content-Url": UPLOAD_URL}, json={})

    def _upload_put(request: httpx.Request) -> httpx.Response:
        order.append("upload_put")
        return httpx.Response(200, json={"videoId": UPLOADED_VIDEO_ID})

    def _playlist_add(request: httpx.Request) -> httpx.Response:
        order.append("playlist_add")
        return httpx.Response(200, json={})

    create_route = respx.post(PLAYLIST_MANAGER).mock(side_effect=_create_playlist)
    init_route = respx.post(STUDIO_UPLOAD_URL).mock(side_effect=_upload_init)
    put_route = respx.put(UPLOAD_URL).mock(side_effect=_upload_put)
    add_route = respx.post(PLAYLIST_ADD).mock(side_effect=_playlist_add)

    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"x" * 2048)

    async with YouTubeClient(settings) as client:
        playlist = await PlaylistService(client).create(
            "My Integration Playlist",
            description="created in test",
        )
        result = await UploadController(client).upload(video_file, title="Integration Video")
        added = await PlaylistService(client).add_video(playlist.playlist_id, result.video_id)

    # -- results -------------------------------------------------------------
    assert playlist.playlist_id == CREATED_PLAYLIST_ID
    assert result.video_id == UPLOADED_VIDEO_ID
    assert added is True

    # -- request ordering (create -> init -> PUT -> add) --------------------
    assert order == [
        "create_playlist",
        "upload_init",
        "upload_put",
        "playlist_add",
    ], f"unexpected request order: {order}"

    # -- payload: playlist creation -----------------------------------------
    assert create_route.called
    create_body = _body_json(create_route)
    assert create_body["title"] == "My Integration Playlist"
    assert create_body["description"] == "created in test"
    assert create_body["privacyStatus"] == "PRIVATE"

    # -- payload: upload init ------------------------------------------------
    assert init_route.called
    init_body = _body_json(init_route)
    assert init_body["videoMetadata"]["title"] == "Integration Video"
    assert init_body["fileSize"] == "2048"

    # -- upload body ---------------------------------------------------------
    assert put_route.call_count == 1
    put_request = put_route.calls.last.request
    assert put_request.headers["Content-Range"] == "bytes 0-2047/2048"
    assert put_request.headers["Content-Length"] == "2048"
    assert put_request.content == b"x" * 2048

    # -- payload: add video to the created playlist -------------------------
    assert add_route.called
    add_body = _body_json(add_route)
    assert add_body["playlistId"] == CREATED_PLAYLIST_ID
    assert add_body["videoId"] == UPLOADED_VIDEO_ID

# --------------------------------------------------------------------------- #
# Flow 1b: multi-chunk upload still ends in a single, complete video
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_flow_upload_multi_chunk(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file larger than one chunk is uploaded across several PUTs, in order."""
    from ytmcp.core import upload as upload_mod

    chunk = 256 * 1024
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", chunk)

    order: list[str] = []
    ranges: list[str] = []

    def _upload_init(request: httpx.Request) -> httpx.Response:
        order.append("init")
        return httpx.Response(200, headers={"X-Upload-Content-Url": UPLOAD_URL}, json={})

    def _upload_put(request: httpx.Request) -> httpx.Response:
        order.append("put")
        ranges.append(request.headers["Content-Range"])
        # First chunk accepted (308), second completes the upload (200).
        if len(ranges) == 1:
            return httpx.Response(308, headers={"Range": f"bytes=0-{chunk - 1}"})
        return httpx.Response(200, json={"videoId": UPLOADED_VIDEO_ID})

    respx.post(STUDIO_UPLOAD_URL).mock(side_effect=_upload_init)
    respx.put(UPLOAD_URL).mock(side_effect=_upload_put)

    # 1.5 chunks -> exactly two PUTs (one full + one partial).
    total = chunk + chunk // 2
    video_file = tmp_path / "big.mp4"
    video_file.write_bytes(b"y" * total)

    async with YouTubeClient(settings) as client:
        result = await UploadController(client).upload(video_file, title="Big Video")

    assert result.video_id == UPLOADED_VIDEO_ID
    assert order == ["init", "put", "put"], f"unexpected order: {order}"
    assert ranges == [
        f"bytes 0-{chunk - 1}/{total}",
        f"bytes {chunk}-{total - 1}/{total}",
    ]

# --------------------------------------------------------------------------- #
# Flow 2: channel info -> list channel videos -> list comments
# --------------------------------------------------------------------------- #
def _channel_page_html(channel_id: str) -> str:
    """A minimal channel page embedding ``ytInitialData``."""
    return (
        "<html><head><script>var ytInitialData = "
        '{"metadata":{"channelMetadataRenderer":{'
        f'"title":"Channel {channel_id}",'
        '"description":"a test channel",'
        '"vanityChannelUrl":"http://www.youtube.com/@testchannel",'
        '"avatar":{"thumbnails":[{"url":"https://img.example.com/av.jpg","width":88}]}'
        "}}};</script></head><body></body></html>"
    )

def _channel_videos_html() -> str:
    """A minimal channel videos page with two ``videoRenderer`` entries."""
    return (
        "<html><head><script>var ytInitialData = "
        '{"contents":{"tabs":[{"tabRenderer":{"content":{"richGridRenderer":{'
        '"contents":['
        '{"richItemRenderer":{"content":{"videoRenderer":{'
        '"videoId":"chanVid1",'
        '"title":{"runs":[{"text":"First Upload"}]},'
        '"ownerText":{"runs":[{"text":"Test Channel"}]},'
        '"lengthText":{"simpleText":"1:30"},'
        '"viewCountText":{"simpleText":"1,234 views"}'
        "}}}},"
        '{"richItemRenderer":{"content":{"videoRenderer":{'
        '"videoId":"chanVid2",'
        '"title":{"runs":[{"text":"Second Upload"}]},'
        '"ownerText":{"runs":[{"text":"Test Channel"}]},'
        '"lengthText":{"simpleText":"2:00"},'
        '"viewCountText":{"simpleText":"42 views"}'
        "}}}}"
        "]}}}}]}};</script></head><body></body></html>"
    )

def _comments_next_json() -> dict[str, Any]:
    return {
        "contents": {
            "twoColumnWatchNextResults": {
                "results": {
                    "results": {
                        "contents": [
                            {
                                "commentThreadRenderer": {
                                    "comment": {
                                        "commentRenderer": {
                                            "commentId": "c1",
                                            "contentText": {"runs": [{"text": "great video"}]},
                                            "authorText": {"runs": [{"text": "Alice"}]},
                                            "voteCount": {"simpleText": "7"},
                                        }
                                    }
                                }
                            }
                        ]
                    }
                }
            }
        }
    }

@pytest.mark.asyncio
@respx.mock
async def test_flow_channel_videos_comments(settings: Settings) -> None:
    """Channel info -> list uploads -> list comments via mocked InnerTube."""
    channel_id = "UCintegrationchannel0001"

    info_route = respx.get(f"https://www.youtube.com/channel/{channel_id}").mock(
        return_value=httpx.Response(200, text=_channel_page_html(channel_id))
    )
    videos_route = respx.get(f"https://www.youtube.com/channel/{channel_id}/videos").mock(
        return_value=httpx.Response(200, text=_channel_videos_html())
    )
    next_route = respx.post(url__regex=NEXT_URL_RE).mock(
        return_value=httpx.Response(200, json=_comments_next_json())
    )

    async with YouTubeClient(settings) as client:
        channel = await ChannelService(client).get_info(channel_id)
        videos = await SearchService(client).list_channel_videos(channel_id)
        comments = await CommentService(client).list_comments("chanVid1", limit=10)

    # -- channel info --------------------------------------------------------
    assert channel.channel_id == channel_id
    assert channel.title == f"Channel {channel_id}"
    assert channel.description == "a test channel"
    assert channel.custom_url == "@testchannel"

    # -- channel videos ------------------------------------------------------
    assert [v.video_id for v in videos] == ["chanVid1", "chanVid2"]
    assert videos[0].title == "First Upload"
    assert videos[0].channel_title == "Test Channel"
    assert videos[0].duration_seconds == 90
    assert videos[0].view_count == 1234
    assert videos[1].duration_seconds == 120
    assert videos[1].view_count == 42

    # -- comments ------------------------------------------------------------
    assert len(comments) == 1
    assert comments[0].comment_id == "c1"
    assert comments[0].text == "great video"
    assert comments[0].author == "Alice"
    assert comments[0].like_count == 7

    # -- every endpoint was exercised ---------------------------------------
    assert info_route.called
    assert videos_route.called
    assert next_route.called

    # -- comments were requested for the same video listed above ------------
    next_body = _body_json(next_route)
    assert next_body["videoId"] == "chanVid1"

# --------------------------------------------------------------------------- #
# Flow 3: cookie file -> AuthResolver.apply -> authenticated request
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_flow_cookie_auth_attaches_cookies(tmp_path: Path) -> None:
    """A cookie file drives AuthResolver.apply -> client carries the cookies."""
    settings = _auth_settings(tmp_path)

    captured: list[str] = []
    authed_url = "https://www.youtube.com/channel/UCauthed000000000000001"

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("cookie", ""))
        return httpx.Response(200, text=_channel_page_html("UCauthed000000000000001"))

    respx.get(authed_url).mock(side_effect=_capture)

    async with YouTubeClient(settings) as client:
        ctx = await AuthResolver(settings).apply(client)
        assert ctx.mode == "cookie"
        assert ctx.is_valid is True

        # The resolved cookies live on the client...
        assert client.cookies["SAPISID"] == "cookie-sapisid"
        assert client.cookies["SID"] == "cookie-sid"

        # ...and are attached to outgoing (mocked) requests.
        resp = await client.get(authed_url)

    assert resp.status_code == 200
    assert captured, "the mocked endpoint was never called"
    header = captured[-1]
    assert "SAPISID=cookie-sapisid" in header
    assert "SID=cookie-sid" in header

# --------------------------------------------------------------------------- #
# Flow 3b: an unauthenticated client sends no cookie header
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_flow_no_auth_sends_no_cookies(settings: Settings) -> None:
    """Without configured auth, no cookie header is sent (contrast to Flow 3)."""
    settings = settings.model_copy(update={"cookie_file": None, "oauth_refresh_token": None})

    seen: list[str | None] = []
    url = "https://www.youtube.com/channel/UCnoauth0000000000000001"

    def _capture(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie"))
        return httpx.Response(200, text=_channel_page_html("UCnoauth0000000000000001"))

    respx.get(url).mock(side_effect=_capture)

    async with YouTubeClient(settings) as client:
        # ``resolve`` is synchronous (it only inspects settings).
        ctx = AuthResolver(settings).resolve()
        assert ctx.mode == "none"
        assert ctx.is_valid is False
        await client.get(url)

    assert seen[-1] in (None, ""), f"unexpected cookie header: {seen[-1]!r}"
