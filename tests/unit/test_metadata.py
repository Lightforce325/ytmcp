"""Tests for core/metadata.py: parsing helpers + MetadataService flows."""

from __future__ import annotations

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core.client import YouTubeClient
from ytmcp.core.exceptions import NotFoundError, UpstreamError
from ytmcp.core.metadata import (
    VIDEO_MANAGER_URL,
    MetadataService,
    _parse_views,
    _runs_text,
)
from ytmcp.core.models import Visibility

DELETE_URL = "https://studio.youtube.com/youtubei/v1/video_manager/delete_video"
NEXT_URL_RE = r".*youtubei/v1/next.*"


def _next_payload(*, title: str | None, description: str | None, views: str | None) -> dict:
    primary: dict = {}
    if title is not None:
        primary["title"] = {"runs": [{"text": title}]}
    if views is not None:
        primary["viewCount"] = {
            "videoViewCountRenderer": {"viewCount": {"runs": [{"text": views}]}}
        }
    secondary: dict = {}
    if description is not None:
        secondary["attributedDescription"] = {"content": description}
    return {
        "contents": {
            "twoColumnWatchNextResults": {
                "results": {
                    "results": {
                        "contents": [
                            {"videoPrimaryInfoRenderer": primary},
                            {"videoSecondaryInfoRenderer": secondary},
                        ]
                    }
                }
            }
        }
    }


# --------------------------------------------------------------------------- #
# _runs_text
# --------------------------------------------------------------------------- #
def test_runs_text_list_of_runs() -> None:
    assert _runs_text([{"text": "Hello "}, {"text": "World"}]) == "Hello World"


def test_runs_text_plain_string() -> None:
    assert _runs_text("plain string") == "plain string"


def test_runs_text_none_returns_empty() -> None:
    assert _runs_text(None) == ""


def test_runs_text_weird_types_return_empty() -> None:
    assert _runs_text(123) == ""
    assert _runs_text({"text": "x"}) == ""
    assert _runs_text([1, 2, {"text": "a"}]) == "a"


def test_runs_text_empty_list() -> None:
    assert _runs_text([]) == ""


# --------------------------------------------------------------------------- #
# _parse_views
# --------------------------------------------------------------------------- #
def test_parse_views_commas() -> None:
    assert _parse_views("1,234,567 views") == 1234567


def test_parse_views_no_digits_returns_none() -> None:
    assert _parse_views("no views") is None
    assert _parse_views("") is None


# --------------------------------------------------------------------------- #
# MetadataService.get_video
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_get_video_parses_title_description_views(settings: Settings) -> None:
    route = respx.post(url__regex=NEXT_URL_RE).mock(
        return_value=httpx.Response(
            200,
            json=_next_payload(
                title="My Great Video",
                description="a description here",
                views="1,234,567 views",
            ),
        )
    )
    async with YouTubeClient(settings) as client:
        video = await MetadataService(client).get_video("vid123")

    assert route.called
    assert video.video_id == "vid123"
    assert video.title == "My Great Video"
    assert video.description == "a description here"
    assert video.view_count == 1234567
    assert video.url == "https://www.youtube.com/watch?v=vid123"


@pytest.mark.asyncio
@respx.mock
async def test_get_video_raises_not_found_when_empty(settings: Settings) -> None:
    respx.post(url__regex=NEXT_URL_RE).mock(
        return_value=httpx.Response(200, json=_next_payload(title=None, description=None, views=None))
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(NotFoundError):
            await MetadataService(client).get_video("missing")


@pytest.mark.asyncio
@respx.mock
async def test_get_video_description_from_runs(settings: Settings) -> None:
    """When attributedDescription is not a string, fall back to description runs."""
    payload = {
        "contents": {
            "twoColumnWatchNextResults": {
                "results": {
                    "results": {
                        "contents": [
                            {"videoPrimaryInfoRenderer": {"title": {"runs": [{"text": "T"}]}}},
                            {
                                "videoSecondaryInfoRenderer": {
                                    "description": {"runs": [{"text": "Line "}, {"text": "two"}]}
                                }
                            },
                        ]
                    }
                }
            }
        }
    }
    respx.post(url__regex=NEXT_URL_RE).mock(return_value=httpx.Response(200, json=payload))
    async with YouTubeClient(settings) as client:
        video = await MetadataService(client).get_video("v")
    assert video.title == "T"
    assert video.description == "Line two"


# --------------------------------------------------------------------------- #
# MetadataService.update_metadata
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_update_metadata_builds_payload_and_sends(settings: Settings) -> None:
    route = respx.post(VIDEO_MANAGER_URL).mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    async with YouTubeClient(settings) as client:
        result = await MetadataService(client).update_metadata(
            "vid1",
            title="New Title",
            description="New Desc",
            tags=["a", "b"],
            category_id="22",
            default_language="en",
        )

    assert route.called
    body = route.calls.last.request.content.decode()
    assert "New Title" in body
    assert "New Desc" in body
    assert "newTags" in body
    assert '"id": "22"' in body or '"id":"22"' in body
    assert "newLanguage" in body
    assert "context" in body

    assert result["video_id"] == "vid1"
    for field in ("videoId", "title", "description", "tags", "category", "language"):
        assert field in result["updated_fields"]


@pytest.mark.asyncio
@respx.mock
async def test_update_metadata_partial_fields(settings: Settings) -> None:
    route = respx.post(VIDEO_MANAGER_URL).mock(
        return_value=httpx.Response(200, json={})
    )
    async with YouTubeClient(settings) as client:
        result = await MetadataService(client).update_metadata("vid1", title="Only Title")

    assert result["updated_fields"] == ["videoId", "title"]
    body = route.calls.last.request.content.decode()
    assert "description" not in body

@pytest.mark.asyncio
@respx.mock
async def test_update_metadata_description_only_omits_title(settings: Settings) -> None:
    """``description`` set while ``title`` is ``None`` skips the title branch.

    Exercises the partial branch where ``if title is not None:`` is False (the
    flow jumps straight to the description check) while the description *is*
    present, so only the description is sent in the payload.
    """
    route = respx.post(VIDEO_MANAGER_URL).mock(
        return_value=httpx.Response(200, json={})
    )
    async with YouTubeClient(settings) as client:
        result = await MetadataService(client).update_metadata(
            "vid1", description="Only Description"
        )

    assert result["updated_fields"] == ["videoId", "description"]
    body = route.calls.last.request.content.decode()
    assert "Only Description" in body
    assert "newDescription" in body
    assert "newTitle" not in body
    assert "title" not in body

@pytest.mark.asyncio
@respx.mock
async def test_update_metadata_title_only_omits_description(settings: Settings) -> None:
    """``title`` set while ``description`` is ``None`` — symmetric case.

    ``if title is not None:`` is True (line 75 -> 76) and ``if description is
    not None:`` is False, so the payload carries only the title.
    """
    route = respx.post(VIDEO_MANAGER_URL).mock(
        return_value=httpx.Response(200, json={})
    )
    async with YouTubeClient(settings) as client:
        result = await MetadataService(client).update_metadata("vid1", title="Only Title")

    assert result["updated_fields"] == ["videoId", "title"]
    body = route.calls.last.request.content.decode()
    assert "newTitle" in body
    assert "Only Title" in body
    assert "newDescription" not in body
    assert "description" not in body


@pytest.mark.asyncio
@respx.mock
async def test_update_metadata_truncates_title(settings: Settings) -> None:
    route = respx.post(VIDEO_MANAGER_URL).mock(
        return_value=httpx.Response(200, json={})
    )
    long_title = "x" * 150
    async with YouTubeClient(settings) as client:
        await MetadataService(client).update_metadata("vid1", title=long_title)
    body = route.calls.last.request.content.decode()
    assert "x" * 100 in body
    assert "x" * 101 not in body


@pytest.mark.asyncio
@respx.mock
async def test_update_metadata_raises_upstream_on_4xx(settings: Settings) -> None:
    respx.post(VIDEO_MANAGER_URL).mock(return_value=httpx.Response(403, text="forbidden"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await MetadataService(client).update_metadata("vid1", title="x")


# --------------------------------------------------------------------------- #
# MetadataService.set_visibility
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_set_visibility_sends_privacy_status(settings: Settings) -> None:
    route = respx.post(VIDEO_MANAGER_URL).mock(
        return_value=httpx.Response(200, json={})
    )
    async with YouTubeClient(settings) as client:
        result = await MetadataService(client).set_visibility("vid1", Visibility.PRIVATE)

    assert route.called
    body = route.calls.last.request.content.decode()
    assert "PRIVATE" in body
    assert "publishAt" not in body
    assert result["visibility"] == "PRIVATE"
    assert result["video_id"] == "vid1"


@pytest.mark.asyncio
@respx.mock
async def test_set_visibility_includes_publish_at(settings: Settings) -> None:
    route = respx.post(VIDEO_MANAGER_URL).mock(
        return_value=httpx.Response(200, json={})
    )
    when = "2030-01-01T00:00:00Z"
    async with YouTubeClient(settings) as client:
        await MetadataService(client).set_visibility(
            "vid1", Visibility.SCHEDULED, publish_at=when
        )
    body = route.calls.last.request.content.decode()
    assert "SCHEDULED" in body
    assert "publishAt" in body
    assert when in body


@pytest.mark.asyncio
@respx.mock
async def test_set_visibility_raises_upstream_on_4xx(settings: Settings) -> None:
    respx.post(VIDEO_MANAGER_URL).mock(return_value=httpx.Response(400, text="bad"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await MetadataService(client).set_visibility("vid1", Visibility.PUBLIC)


# --------------------------------------------------------------------------- #
# MetadataService.delete_video
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_delete_video_success(settings: Settings) -> None:
    route = respx.post(DELETE_URL).mock(return_value=httpx.Response(200, json={}))
    async with YouTubeClient(settings) as client:
        ok = await MetadataService(client).delete_video("vid1")

    assert ok is True
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "vid1" in body
    assert "context" in body


@pytest.mark.asyncio
@respx.mock
async def test_delete_video_raises_upstream_on_4xx(settings: Settings) -> None:
    respx.post(DELETE_URL).mock(return_value=httpx.Response(404, text="gone"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await MetadataService(client).delete_video("vid1")


# --------------------------------------------------------------------------- #
# _studio_body
# --------------------------------------------------------------------------- #
def test_studio_body_includes_context(settings: Settings) -> None:
    service = MetadataService(YouTubeClient(settings))
    body = service._studio_body({"videoId": "abc"})
    assert "context" in body
    assert body["context"]["client"]["clientName"] == "WEB"
    assert body["videoId"] == "abc"
