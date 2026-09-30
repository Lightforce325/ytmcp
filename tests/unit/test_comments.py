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


def test_walk_comments_keeps_replies() -> None:
    """Regression: replies inside a commentThreadRenderer must NOT be lost.

    A previous fix used ``continue`` for the whole ``commentThreadRenderer``,
    which discarded ``replies`` entirely. The main comment must appear once and
    every reply must be surfaced.
    """
    node = {
        "commentThreadRenderer": {
            "comment": {
                "commentRenderer": {
                    "commentId": "TOP",
                    "contentText": {"runs": [{"text": "top comment"}]},
                }
            },
            "replies": {
                "commentRepliesRenderer": {
                    "contents": [
                        {
                            "commentRenderer": {
                                "commentId": "REPLY1",
                                "contentText": {"runs": [{"text": "first reply"}]},
                            }
                        },
                        {
                            "commentRenderer": {
                                "commentId": "REPLY2",
                                "contentText": {"runs": [{"text": "second reply"}]},
                            }
                        },
                    ]
                }
            },
        }
    }
    comments = _walk_comments(node)
    ids = [c.comment_id for c in comments]
    assert ids == ["TOP", "REPLY1", "REPLY2"], f"unexpected ids: {ids}"
    # no duplicates
    assert len(ids) == len(set(ids)), f"duplicates present: {ids}"
    # reply text is preserved
    texts = {c.comment_id: c.text for c in comments}
    assert texts["REPLY1"] == "first reply"
    assert texts["REPLY2"] == "second reply"

def test_walk_comments_thread_with_replies_no_duplicates() -> None:
    """Ensure the main comment is emitted exactly once even with replies."""
    node = {
        "commentThreadRenderer": {
            "comment": {"commentRenderer": {"commentId": "only-once"}},
            "replies": {
                "commentRepliesRenderer": {
                    "contents": [{"commentRenderer": {"commentId": "r1"}}]
                }
            },
        }
    }
    comments = _walk_comments(node)
    ids = [c.comment_id for c in comments]
    assert ids.count("only-once") == 1
    assert ids == ["only-once", "r1"]

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

def test_walk_comments_thread_renderer_non_dict_falls_through() -> None:
    """A ``commentThreadRenderer`` whose value is NOT a dict must not crash.

    Branch ``98 -> 101``: the ``isinstance(v, dict)`` guard is False, so the
    wrapper is skipped (``continue``) rather than recursed into. A non-dict
    wrapper is meaningless, but the walk must stay safe and not raise.
    """
    # string value -> skipped, no comments, no crash.
    assert _walk_comments({"commentThreadRenderer": "bukan-dict"}) == []

    # a list value -> not a dict; the wrapper is skipped (arc 98->101) and the
    # remaining keys of the enclosing dict are still walked.
    node_list = {
        "commentThreadRenderer": [{"commentRenderer": {"commentId": "inner"}}],
        "other": {"commentRenderer": {"commentId": "sibling"}},
    }
    ids = [c.comment_id for c in _walk_comments(node_list)]
    assert ids == ["sibling"], f"non-dict wrapper must be skipped, got {ids}"

def test_walk_comments_non_dict_wrapper_keeps_sibling_comments() -> None:
    """Non-dict ``commentThreadRenderer`` must not swallow sibling entries."""
    node = {
        "commentThreadRenderer": ["junk"],
        "other": {"commentRenderer": {"commentId": "sibling"}},
    }
    ids = [c.comment_id for c in _walk_comments(node)]
    assert ids == ["sibling"]


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
async def test_list_comments_includes_replies(settings: Settings) -> None:
    """End-to-end: replies nested in a thread must reach list_comments output."""
    respx.post(url__regex=r".*youtubei/v1/next.*").mock(
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
                                                    "commentId": "TOP",
                                                    "contentText": {"runs": [{"text": "top"}]},
                                                }
                                            },
                                            "replies": {
                                                "commentRepliesRenderer": {
                                                    "contents": [
                                                        {
                                                            "commentRenderer": {
                                                                "commentId": "R1",
                                                                "contentText": {
                                                                    "runs": [{"text": "reply"}]
                                                                },
                                                            }
                                                        }
                                                    ]
                                                }
                                            },
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
    ids = [c.comment_id for c in comments]
    assert ids == ["TOP", "R1"], f"expected replies surfaced, got {ids}"

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

# --------------------------------------------------------------------------- #
# list_comments: empty section (line 38) & continuation fallback (lines 42-49)
# --------------------------------------------------------------------------- #
_EMPTY_WATCH_JSON = {"contents": {"twoColumnWatchNextResults": {"results": {}}}}
_CONTINUATION_JSON = {
    "onResponseReceivedEndpoints": [
        {
            "appendContinuationItemsAction": {
                "continuationItems": [
                    {
                        "commentThreadRenderer": {
                            "comment": {
                                "commentRenderer": {
                                    "commentId": "cont1",
                                    "contentText": {"runs": [{"text": "dari continuation"}]},
                                    "authorText": {"runs": [{"text": "Sari"}]},
                                    "voteCount": {"simpleText": "7"},
                                }
                            }
                        }
                    }
                ]
            }
        }
    ]
}

@pytest.mark.asyncio
@respx.mock
async def test_list_comments_uses_continuation_when_section_empty(
    settings: Settings,
) -> None:
    """When the watch page yields no comments, the continuation is fetched.

    Covers branch ``if not found:`` (line 38) plus the success path of
    ``_comments_via_continuation`` (lines 42-49). The first ``next`` call has an
    empty section; the second (continuation, payload ``params='MgIQABoA'``)
    carries the comments.
    """
    route = respx.post(url__regex=r".*youtubei/v1/next.*").mock(
        side_effect=[
            httpx.Response(200, json=_EMPTY_WATCH_JSON),
            httpx.Response(200, json=_CONTINUATION_JSON),
        ]
    )
    async with YouTubeClient(settings) as client:
        comments = await CommentService(client).list_comments("vid1", limit=10)
    ids = [c.comment_id for c in comments]
    assert ids == ["cont1"], f"expected continuation comments, got {ids}"
    assert comments[0].text == "dari continuation"
    assert comments[0].like_count == 7
    # two InnerTube calls: watch page + continuation
    assert route.call_count == 2
    second_body = route.calls.last.request.content.decode()
    assert "MgIQABoA" in second_body, "continuation must carry the params token"

@pytest.mark.asyncio
@respx.mock
async def test_list_comments_continuation_respects_limit(settings: Settings) -> None:
    """The continuation result is truncated to ``limit``."""
    items = [
        {
            "commentRenderer": {
                "commentId": f"cc{i}",
                "contentText": {"runs": [{"text": str(i)}]},
            }
        }
        for i in range(5)
    ]
    respx.post(url__regex=r".*youtubei/v1/next.*").mock(
        side_effect=[
            httpx.Response(200, json=_EMPTY_WATCH_JSON),
            httpx.Response(
                200,
                json={
                    "onResponseReceivedEndpoints": [
                        {"appendContinuationItemsAction": {"continuationItems": items}}
                    ]
                },
            ),
        ]
    )
    async with YouTubeClient(settings) as client:
        comments = await CommentService(client).list_comments("vid1", limit=2)
    assert len(comments) == 2
    assert [c.comment_id for c in comments] == ["cc0", "cc1"]

@pytest.mark.asyncio
@respx.mock
async def test_list_comments_continuation_empty_returns_empty(
    settings: Settings,
) -> None:
    """If the continuation also has no comments, ``list_comments`` returns []."""
    respx.post(url__regex=r".*youtubei/v1/next.*").mock(
        return_value=httpx.Response(200, json={"contents": {"results": {}}})
    )
    async with YouTubeClient(settings) as client:
        comments = await CommentService(client).list_comments("vid1", limit=10)
    assert comments == []

@pytest.mark.asyncio
async def test_list_comments_section_without_contents_key(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Line 38: the ``.get('contents', [])`` chain must tolerate missing keys.

    The watch page has no ``twoColumnWatchNextResults`` at all, so the section
    resolves to ``[]`` and the continuation fallback is taken. ``_walk_comments``
    is the empty result.
    """
    calls: list[str] = []

    async def _fake_innertube(endpoint: str, payload: dict) -> dict:
        calls.append(payload.get("params", ""))
        return {"contents": {"unrelatedSection": {}}}

    async def _fake_cont(video_id: str, limit: int):
        calls.append("continuation")
        return []

    async with YouTubeClient(settings) as client:
        service = CommentService(client)
        monkeypatch.setattr(client, "innertube", _fake_innertube)
        monkeypatch.setattr(service, "_comments_via_continuation", _fake_cont)
        result = await service.list_comments("vid1", limit=10)

    assert result == []
    assert "continuation" in calls, "empty section must trigger continuation"

@pytest.mark.asyncio
async def test_comments_via_continuation_success(settings: Settings) -> None:
    """``_comments_via_continuation`` happy path (lines 42-49)."""

    async def _fake_innertube(endpoint: str, payload: dict) -> dict:
        assert endpoint == "next"
        assert payload["params"] == "MgIQABoA"
        return _CONTINUATION_JSON

    async with YouTubeClient(settings) as client:
        service = CommentService(client)
        service.client.innertube = _fake_innertube  # type: ignore[method-assign]
        result = await service._comments_via_continuation("vid1", 10)
    assert [c.comment_id for c in result] == ["cont1"]

@pytest.mark.asyncio
async def test_comments_via_continuation_returns_empty_on_exception(
    settings: Settings,
) -> None:
    """``except Exception`` branch (lines 46-48) must yield ``[]``, not raise."""

    async def _boom(endpoint: str, payload: dict) -> dict:
        raise httpx.ConnectError("network down")

    async with YouTubeClient(settings) as client:
        service = CommentService(client)
        service.client.innertube = _boom  # type: ignore[method-assign]
        result = await service._comments_via_continuation("vid1", 10)
    assert result == []
