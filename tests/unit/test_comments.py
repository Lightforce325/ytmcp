"""Tests for core/comments.py: parsers and CommentService endpoints."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core.client import YouTubeClient
from ytmcp.core.comments import (
    COMMENT_CREATE,
    COMMENT_SET_STATE,
    CommentService,
    _encode_create_reply_params,
    _parse_comment,
    _runs_text,
    _walk_comments,
)
from ytmcp.core.exceptions import UpstreamError


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #
def test_runs_text_list() -> None:
    assert _runs_text([{"text": "Hello "}, {"text": "World"}]) == "Hello World"


def test_runs_text_string() -> None:
    assert _runs_text("plain text") == "plain text"


def test_runs_text_none_and_invalid() -> None:
    assert _runs_text(None) == ""
    assert _runs_text(123) == ""
    # non-dict entries inside a list are ignored
    assert _runs_text([{"text": "a"}, "junk", {"text": "b"}]) == "ab"


def test_walk_comments_extracts_comment_renderer() -> None:
    node = {
        "commentThreadRenderer": {
            "comment": {
                "commentRenderer": {
                    "commentId": "Ug1",
                    "contentText": {"runs": [{"text": "nice video"}]},
                    "authorText": {"runs": [{"text": "Budi"}]},
                    "voteCount": {"simpleText": "12"},
                }
            }
        }
    }
    comments = _walk_comments(node)
    assert len(comments) == 1
    assert comments[0].comment_id == "Ug1"
    assert comments[0].text == "nice video"
    assert comments[0].author == "Budi"
    assert comments[0].like_count == 12


def test_walk_comments_thread_renderer_not_duplicated() -> None:
    """Regression: a commentThreadRenderer wraps an inner commentRenderer.

    Recursing into the wrapper used to emit the same comment twice; the
    wrapper's inner payload must not be walked again.
    """
    node = {
        "commentThreadRenderer": {
            "comment": {
                "commentRenderer": {
                    "commentId": "dup1",
                    "contentText": {"runs": [{"text": "once"}]},
                }
            }
        }
    }
    comments = _walk_comments(node)
    assert len(comments) == 1, f"expected 1 comment, got {len(comments)}: {comments}"


def test_walk_comments_handles_nested_lists() -> None:
    node = {
        "contents": [
            {"commentRenderer": {"commentId": "c1", "contentText": {"runs": [{"text": "a"}]}}},
            {"wrapper": {"commentRenderer": {"commentId": "c2", "contentText": {"runs": [{"text": "b"}]}}}},
        ]
    }
    comments = _walk_comments(node)
    ids = {c.comment_id for c in comments}
    assert ids == {"c1", "c2"}


def test_walk_comments_empty_when_absent() -> None:
    assert _walk_comments({"foo": "bar"}) == []
    assert _walk_comments([]) == []
    assert _walk_comments("string") == []


def test_parse_comment_from_top_level_renderer() -> None:
    # renderer dict where entity itself is the commentRenderer
    renderer = {
        "commentId": "abc",
        "contentText": {"runs": [{"text": "hi"}]},
        "authorText": {"runs": [{"text": "Ani"}]},
        "voteCount": {"simpleText": "1.2K"},
    }
    c = _parse_comment(renderer)
    assert c.comment_id == "abc"
    assert c.text == "hi"
    assert c.author == "Ani"
    # digits extracted -> 12
    assert c.like_count == 12


def test_parse_comment_defaults_when_missing() -> None:
    c = _parse_comment({})
    assert c.comment_id == ""
    assert c.text == ""
    assert c.author == ""
    assert c.like_count == 0


def test_encode_create_reply_params_roundtrip() -> None:
    encoded = _encode_create_reply_params("vid123", "cid456")
    decoded = base64.urlsafe_b64decode(encoded).decode()
    assert decoded == "vid123|cid456"


# --------------------------------------------------------------------------- #
# CommentService.reply / moderate
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_reply_sends_correct_payload(settings: Settings) -> None:
    route = respx.post(COMMENT_CREATE).mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    async with YouTubeClient(settings) as client:
        comment = await CommentService(client).reply("vid1", "cid1", "halo!")
        assert comment.is_reply is True
        assert comment.video_id == "vid1"
        assert comment.text == "halo!"

    assert route.called
    sent = route.calls.last.request
    body = sent.content.decode()
    assert "halo!" in body
    assert "createReplyParams" in body
    # the encoded params must decode back to video|comment
    expected = _encode_create_reply_params("vid1", "cid1")
    assert expected in body


@pytest.mark.asyncio
@respx.mock
async def test_reply_raises_on_4xx(settings: Settings) -> None:
    respx.post(COMMENT_CREATE).mock(return_value=httpx.Response(403, text="forbidden"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await CommentService(client).reply("v", "c", "x")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["published", "heldForReview", "rejected"])
@respx.mock
async def test_moderate_sends_action(settings: Settings, action: str) -> None:
    route = respx.post(COMMENT_SET_STATE).mock(
        return_value=httpx.Response(200, json={})
    )
    async with YouTubeClient(settings) as client:
        ok = await CommentService(client).moderate("cid1", action)
        assert ok is True

    body = route.calls.last.request.content.decode()
    assert action in body
    assert "cid1" in body


@pytest.mark.asyncio
@respx.mock
async def test_moderate_raises_on_4xx(settings: Settings) -> None:
    respx.post(COMMENT_SET_STATE).mock(return_value=httpx.Response(400))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await CommentService(client).moderate("cid", "published")


# --------------------------------------------------------------------------- #
# list_comments (innertube mock)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_list_comments_from_watch_page(settings: Settings) -> None:
    innertube_url = respx.post(url__regex=r".*youtubei/v1/next.*")
    innertube_url.mock(
        return_value=httpx.Response(
            200,
            json={
                "contents": {
                    "twoColumnWatchNextResults": {
                        "results": {
                            "results": {
                                "contents": [
                                    {
                                        "commentThreadRenderer": {
                                            "comment": {
                                                "commentRenderer": {
                                                    "commentId": "x1",
                                                    "contentText": {"runs": [{"text": "wow"}]},
                                                    "authorText": {"runs": [{"text": "Dewi"}]},
                                                    "voteCount": {"simpleText": "3"},
                                                }
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    }
                }
            },
        )
    )
    async with YouTubeClient(settings) as client:
        comments = await CommentService(client).list_comments("vid1", limit=10)
    assert len(comments) == 1
    assert comments[0].comment_id == "x1"
    assert comments[0].text == "wow"


@pytest.mark.asyncio
@respx.mock
async def test_list_comments_respects_limit(settings: Settings) -> None:
    many = [
        {"commentRenderer": {"commentId": f"c{i}", "contentText": {"runs": [{"text": str(i)}]}}}
        for i in range(5)
    ]
    respx.post(url__regex=r".*youtubei/v1/next.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "contents": {
                    "twoColumnWatchNextResults": {
                        "results": {"results": {"contents": many}}
                    }
                }
            },
        )
    )
    async with YouTubeClient(settings) as client:
        comments = await CommentService(client).list_comments("vid1", limit=2)
    assert len(comments) == 2
