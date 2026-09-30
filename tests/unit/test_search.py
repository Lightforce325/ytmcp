"""Tests for :mod:`ytmcp.core.search` (parsers + SearchService)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core.client import YouTubeClient
from ytmcp.core.exceptions import UpstreamError
from ytmcp.core.search import (
    SearchService,
    _parse_channel_videos,
    _parse_duration,
    _runs,
    _to_int,
    _walk_search,
)

CHANNEL_VIDEOS_URL = "https://www.youtube.com/channel/UC1234567890123456789012/videos"

# --------------------------------------------------------------------------- #
# helpers: _runs / _to_int / _parse_duration
# --------------------------------------------------------------------------- #

def test_runs_joins_text() -> None:
    assert _runs([{"text": "Hello "}, {"text": "world"}]) == "Hello world"

def test_runs_string_passthrough() -> None:
    assert _runs("plain") == "plain"

def test_runs_none_and_invalid() -> None:
    assert _runs(None) == ""
    assert _runs(123) == ""
    assert _runs([1, 2]) == ""

def test_to_int_none() -> None:
    assert _to_int(None) is None

def test_to_int_extracts_digits() -> None:
    assert _to_int("1,234 views") == 1234
    assert _to_int("42") == 42

def test_to_int_no_digits_returns_none() -> None:
    assert _to_int("no views") is None

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("abc", None),
        ("1:2:3", 3723),
        ("10:07", 607),
        ("0:59", 59),
        ("1:00:00", 3600),
        ("59:59", 3599),
    ],
)
def test_parse_duration(text: str, expected: int | None) -> None:
    assert _parse_duration(text) == expected

def test_parse_duration_very_long() -> None:
    # 100 hours worth of digits still parses deterministically.
    assert _parse_duration("100:00:00") == 360000

# --------------------------------------------------------------------------- #
# _walk_search
# --------------------------------------------------------------------------- #

def test_walk_search_extracts_video_renderer() -> None:
    section = {
        "contents": [
            {
                "videoRenderer": {
                    "videoId": "abc123",
                    "title": {"runs": [{"text": "My Video"}]},
                    "detailedMetadataSnippets": [
                        {"snippetText": {"runs": [{"text": "desc part"}]}}
                    ],
                    "ownerText": {"runs": [{"text": "Owner"}]},
                    "lengthText": {"simpleText": "3:21"},
                    "viewCountText": {"simpleText": "1,234 views"},
                }
            }
        ]
    }
    out: list = []
    _walk_search(section, out)
    assert len(out) == 1
    video = out[0]
    assert video.video_id == "abc123"
    assert video.title == "My Video"
    assert video.description == "desc part"
    assert video.channel_title == "Owner"
    assert video.duration_seconds == 201
    assert video.view_count == 1234
    assert video.url == "https://www.youtube.com/watch?v=abc123"

def test_walk_search_falls_back_to_long_byline() -> None:
    section = {
        "videoRenderer": {
            "videoId": "x1",
            "title": {"runs": [{"text": "T"}]},
            "longBylineText": {"runs": [{"text": "Byline Channel"}]},
        }
    }
    out: list = []
    _walk_search(section, out)
    assert out[0].channel_title == "Byline Channel"
    assert out[0].description == ""
    assert out[0].duration_seconds is None
    assert out[0].view_count is None

def test_walk_search_scalars_and_empty() -> None:
    out: list = []
    _walk_search("string", out)
    _walk_search(42, out)
    _walk_search([], out)
    assert out == []

def test_walk_search_nested_multiple() -> None:
    section = {
        "a": {"videoRenderer": {"videoId": "1"}},
        "b": [{"c": {"videoRenderer": {"videoId": "2"}}}],
    }
    out: list = []
    _walk_search(section, out)
    assert {v.video_id for v in out} == {"1", "2"}

# --------------------------------------------------------------------------- #
# _parse_channel_videos
# --------------------------------------------------------------------------- #

def test_parse_channel_videos_valid() -> None:
    data = {"contents": [{"videoRenderer": {"videoId": "v1", "title": {"runs": [{"text": "V1"}]}}}]}
    html = f"<script>var ytInitialData = {json.dumps(data)};</script>"
    videos = _parse_channel_videos(html)
    assert len(videos) == 1
    assert videos[0].video_id == "v1"
    assert videos[0].title == "V1"

def test_parse_channel_videos_no_script_returns_empty() -> None:
    assert _parse_channel_videos("<html>nothing</html>") == []

def test_parse_channel_videos_invalid_json_returns_empty() -> None:
    html = "<script>var ytInitialData = {bad json};</script>"
    assert _parse_channel_videos(html) == []

# --------------------------------------------------------------------------- #
# SearchService.search (InnerTube)
# --------------------------------------------------------------------------- #

def _search_payload() -> dict:
    return {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "contents": [
                        {
                            "videoRenderer": {
                                "videoId": "vid001",
                                "title": {"runs": [{"text": "First"}]},
                                "ownerText": {"runs": [{"text": "Chan"}]},
                                "lengthText": {"simpleText": "1:00"},
                                "viewCountText": {"simpleText": "10 views"},
                            }
                        },
                        {
                            "videoRenderer": {
                                "videoId": "vid002",
                                "title": {"runs": [{"text": "Second"}]},
                            }
                        },
                    ]
                }
            }
        }
    }

@pytest.mark.asyncio
@respx.mock
async def test_search_returns_videos(settings: Settings) -> None:
    route = respx.post(url__regex=r".*youtubei/v1/search.*").mock(
        return_value=httpx.Response(200, json=_search_payload())
    )
    async with YouTubeClient(settings) as client:
        videos = await SearchService(client).search("first")

    assert [v.video_id for v in videos] == ["vid001", "vid002"]
    assert videos[0].title == "First"
    assert videos[0].channel_title == "Chan"
    assert videos[0].duration_seconds == 60
    assert videos[0].view_count == 10
    body = json.loads(route.calls.last.request.content.decode())
    assert body["query"] == "first"

@pytest.mark.asyncio
@respx.mock
async def test_search_respects_limit(settings: Settings) -> None:
    respx.post(url__regex=r".*youtubei/v1/search.*").mock(
        return_value=httpx.Response(200, json=_search_payload())
    )
    async with YouTubeClient(settings) as client:
        videos = await SearchService(client).search("q", limit=1)
    assert len(videos) == 1
    assert videos[0].video_id == "vid001"

@pytest.mark.asyncio
@respx.mock
async def test_search_empty_payload(settings: Settings) -> None:
    respx.post(url__regex=r".*youtubei/v1/search.*").mock(
        return_value=httpx.Response(200, json={})
    )
    async with YouTubeClient(settings) as client:
        assert await SearchService(client).search("nothing") == []

# --------------------------------------------------------------------------- #
# SearchService.list_channel_videos
# --------------------------------------------------------------------------- #

def _channel_videos_html() -> str:
    data = {
        "contents": [
            {"videoRenderer": {"videoId": "c1", "title": {"runs": [{"text": "C1"}]}}},
            {"videoRenderer": {"videoId": "c2", "title": {"runs": [{"text": "C2"}]}}},
        ]
    }
    return f"<script>var ytInitialData = {json.dumps(data)};</script>"

@pytest.mark.asyncio
@respx.mock
async def test_list_channel_videos_success(settings: Settings) -> None:
    route = respx.get(CHANNEL_VIDEOS_URL).mock(
        return_value=httpx.Response(200, text=_channel_videos_html())
    )
    async with YouTubeClient(settings) as client:
        videos = await SearchService(client).list_channel_videos("UC1234567890123456789012")

    assert [v.video_id for v in videos] == ["c1", "c2"]
    assert route.called

@pytest.mark.asyncio
@respx.mock
async def test_list_channel_videos_limit(settings: Settings) -> None:
    respx.get(CHANNEL_VIDEOS_URL).mock(
        return_value=httpx.Response(200, text=_channel_videos_html())
    )
    async with YouTubeClient(settings) as client:
        videos = await SearchService(client).list_channel_videos(
            "UC1234567890123456789012", limit=1
        )
    assert len(videos) == 1
    assert videos[0].video_id == "c1"

@pytest.mark.asyncio
@respx.mock
async def test_list_channel_videos_4xx_raises(settings: Settings) -> None:
    respx.get(CHANNEL_VIDEOS_URL).mock(return_value=httpx.Response(404))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await SearchService(client).list_channel_videos("UC1234567890123456789012")
