"""Tests for :mod:`ytmcp.core.channel` (parsers + ChannelService)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core.channel import (
    ChannelService,
    _extract_yt_initial_data,
    _find_channel_header,
)
from ytmcp.core.client import YouTubeClient
from ytmcp.core.exceptions import NotFoundError, UpstreamError

CHANNEL_URL = "https://www.youtube.com/channel/UC1234567890123456789012"
ACCOUNT_URL = "https://www.youtube.com/account"

# --------------------------------------------------------------------------- #
# _extract_yt_initial_data
# --------------------------------------------------------------------------- #

def test_extract_var_assignment_pattern() -> None:
    html = "<html><script>var ytInitialData = {\"a\": 1};</script></html>"
    assert _extract_yt_initial_data(html) == {"a": 1}

def test_extract_bracket_assignment_pattern() -> None:
    html = '<html><script>window["ytInitialData"] = {"b": 2};</script></html>'
    assert _extract_yt_initial_data(html) == {"b": 2}

def test_extract_window_bracket_pattern() -> None:
    html = '<html><script>x["ytInitialData"] = {"c": 3};</script></html>'
    assert _extract_yt_initial_data(html) == {"c": 3}

def test_extract_nested_json() -> None:
    payload = {"metadata": {"channelMetadataRenderer": {"title": "Nested"}}}
    html = f"<script>var ytInitialData = {json.dumps(payload)};</script>"
    assert _extract_yt_initial_data(html) == payload

def test_extract_not_found_returns_empty() -> None:
    assert _extract_yt_initial_data("<html>no data here</html>") == {}
    assert _extract_yt_initial_data("") == {}

def test_extract_invalid_json_returns_empty() -> None:
    html = "<script>var ytInitialData = {not valid json};</script>"
    assert _extract_yt_initial_data(html) == {}

# --------------------------------------------------------------------------- #
# _find_channel_header
# --------------------------------------------------------------------------- #

def test_find_channel_header_present() -> None:
    header = {"banner": {"imageBanner": {"thumbnails": []}}}
    data = {"header": {"c4TabbedHeaderRenderer": header}}
    assert _find_channel_header(data) == header

def test_find_channel_header_missing() -> None:
    assert _find_channel_header({}) == {}
    assert _find_channel_header({"header": {}}) == {}

def test_find_channel_header_wrong_type() -> None:
    assert _find_channel_header({"header": None}) == {}
    assert _find_channel_header({"header": {"c4TabbedHeaderRenderer": "nope"}}) == {}

# --------------------------------------------------------------------------- #
# ChannelService.get_info
# --------------------------------------------------------------------------- #

def _channel_html() -> str:
    data = {
        "metadata": {
            "channelMetadataRenderer": {
                "title": "Test Channel",
                "description": "A channel used for tests",
                "avatar": {
                    "thumbnails": [
                        {"url": "https://img/small.jpg", "width": 88},
                        {"url": "https://img/large.jpg", "width": 900},
                    ]
                },
                "vanityChannelUrl": "http://www.youtube.com/@testchannel",
            }
        },
        "header": {
            "c4TabbedHeaderRenderer": {
                "banner": {
                    "imageBanner": {
                        "thumbnails": [
                            {"url": "https://img/banner-small.jpg"},
                            {"url": "https://img/banner-large.jpg"},
                        ]
                    }
                }
            }
        },
    }
    return f"<script>var ytInitialData = {json.dumps(data)};</script>"

@pytest.mark.asyncio
@respx.mock
async def test_get_info_success(settings: Settings) -> None:
    respx.get(CHANNEL_URL).mock(return_value=httpx.Response(200, text=_channel_html()))
    async with YouTubeClient(settings) as client:
        channel = await ChannelService(client).get_info("UC1234567890123456789012")

    assert channel.channel_id == "UC1234567890123456789012"
    assert channel.title == "Test Channel"
    assert channel.description == "A channel used for tests"
    assert channel.avatar_url == "https://img/large.jpg"
    assert channel.banner_url == "https://img/banner-large.jpg"
    assert channel.custom_url == "@testchannel"
    assert channel.country is None

@pytest.mark.asyncio
@respx.mock
async def test_get_info_resolves_own_channel_when_none(settings: Settings) -> None:
    own = "UCabcdefghijklmnopqrstuv"
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, text=_own_channel_html(own)))
    respx.get(f"https://www.youtube.com/channel/{own}").mock(
        return_value=httpx.Response(200, text=_channel_html())
    )
    async with YouTubeClient(settings) as client:
        channel = await ChannelService(client).get_info()
    assert channel.channel_id == own
    assert channel.title == "Test Channel"

@pytest.mark.asyncio
@respx.mock
async def test_get_info_404_raises_not_found(settings: Settings) -> None:
    respx.get(CHANNEL_URL).mock(return_value=httpx.Response(404))
    async with YouTubeClient(settings) as client:
        with pytest.raises(NotFoundError):
            await ChannelService(client).get_info("UC1234567890123456789012")

@pytest.mark.asyncio
@respx.mock
async def test_get_info_400_raises_upstream_error(settings: Settings) -> None:
    respx.get(CHANNEL_URL).mock(return_value=httpx.Response(400))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await ChannelService(client).get_info("UC1234567890123456789012")

@pytest.mark.asyncio
@respx.mock
async def test_get_info_empty_html_yields_defaults(settings: Settings) -> None:
    respx.get(CHANNEL_URL).mock(return_value=httpx.Response(200, text="<html></html>"))
    async with YouTubeClient(settings) as client:
        channel = await ChannelService(client).get_info("UC1234567890123456789012")

    assert channel.title == ""
    assert channel.avatar_url is None
    assert channel.banner_url is None
    assert channel.custom_url is None

# --------------------------------------------------------------------------- #
# ChannelService.get_own_channel_id
# --------------------------------------------------------------------------- #

def _own_channel_html(own_id: str) -> str:
    data = {
        "header": {
            "pageHeaderRenderer": {
                "content": {
                    "pageHeaderViewModel": {
                        "metadata": {
                            "contentMetadataViewModel": {
                                "metadataRows": [
                                    {"metadataParts": [{"text": {"content": own_id}}]}
                                ]
                            }
                        }
                    }
                }
            }
        }
    }
    return f"<script>var ytInitialData = {json.dumps(data)};</script>"

@pytest.mark.asyncio
@respx.mock
async def test_get_own_channel_id_via_page_header(settings: Settings) -> None:
    own = "UCabcdefghijklmnopqrstuv"
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, text=_own_channel_html(own)))
    async with YouTubeClient(settings) as client:
        assert await ChannelService(client).get_own_channel_id() == own

@pytest.mark.asyncio
@respx.mock
async def test_get_own_channel_id_regex_fallback(settings: Settings) -> None:
    own = "UCabcdefghijklmnopqrstuv"
    html = f'<html>\n<script>var x = {{"channelId":"{own}"}};</script></html>'
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, text=html))
    async with YouTubeClient(settings) as client:
        assert await ChannelService(client).get_own_channel_id() == own

@pytest.mark.asyncio
@respx.mock
async def test_get_own_channel_id_failure_raises(settings: Settings) -> None:
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, text="<html>nothing</html>"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await ChannelService(client).get_own_channel_id()

@pytest.mark.asyncio
@respx.mock
async def test_get_own_channel_id_no_channelid_line_raises(settings: Settings) -> None:
    # A page whose text contains no "channelId" line at all exercises the loop
    # exiting without match.
    respx.get(ACCOUNT_URL).mock(
        return_value=httpx.Response(200, text="<html>\n<script>{}</script></html>")
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await ChannelService(client).get_own_channel_id()

# --------------------------------------------------------------------------- #
# ChannelService.get_subscriber_count
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@respx.mock
async def test_get_subscriber_count_delegates_to_get_info(settings: Settings) -> None:
    respx.get(CHANNEL_URL).mock(return_value=httpx.Response(200, text=_channel_html()))
    async with YouTubeClient(settings) as client:
        count = await ChannelService(client).get_subscriber_count("UC1234567890123456789012")
    # channel.html carries no subscriber count -> None
    assert count is None

# --------------------------------------------------------------------------- #
# ChannelService.update_branding + internals
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_update_branding_empty_returns_empty(settings: Settings) -> None:
    client = YouTubeClient(settings)
    assert await ChannelService(client).update_branding() == {}

@pytest.mark.asyncio
async def test_update_branding_uploads_and_sets_links(settings: Settings) -> None:
    client = YouTubeClient(settings)
    links = [{"title": "Site", "url": "https://example.com"}]
    result = await ChannelService(client).update_branding(
        banner_path="/tmp/banner.png",
        avatar_path="/tmp/avatar.png",
        links=links,
    )
    assert result["banner"] == {"kind": "banner", "path": "/tmp/banner.png", "status": "staged"}
    assert result["avatar"] == {"kind": "avatar", "path": "/tmp/avatar.png", "status": "staged"}
    assert result["links"] == links

@pytest.mark.asyncio
async def test_update_branding_banner_only(settings: Settings) -> None:
    client = YouTubeClient(settings)
    result = await ChannelService(client).update_branding(banner_path="/tmp/b.png")
    assert set(result) == {"banner"}

@pytest.mark.asyncio
async def test_upload_studio_asset_stages(settings: Settings) -> None:
    client = YouTubeClient(settings)
    asset = await ChannelService(client)._upload_studio_asset("/tmp/x.png", "avatar")
    assert asset == {"kind": "avatar", "path": "/tmp/x.png", "status": "staged"}

@pytest.mark.asyncio
async def test_set_channel_links_returns_input(settings: Settings) -> None:
    client = YouTubeClient(settings)
    links = [{"title": "A", "url": "https://a"}, {"title": "B", "url": "https://b"}]
    assert await ChannelService(client)._set_channel_links(links) == links
