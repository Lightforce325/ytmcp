"""Tests for models and their computed properties."""

from __future__ import annotations

from ytmcp.core.models import Channel, Comment, Video, Visibility


def test_video_watch_url_from_id() -> None:
    v = Video(video_id="abc123")
    assert v.watch_url == "https://www.youtube.com/watch?v=abc123"


def test_video_watch_url_explicit() -> None:
    v = Video(video_id="abc123", url="https://youtu.be/abc123")
    assert v.watch_url == "https://youtu.be/abc123"


def test_visibility_enum() -> None:
    assert Visibility("public".upper()) is Visibility.PUBLIC
    assert Visibility.PRIVATE.value == "PRIVATE"


def test_channel_defaults() -> None:
    c = Channel(channel_id="UC123")
    assert c.title == ""
    assert c.video_count is None


def test_comment_defaults() -> None:
    c = Comment(comment_id="c1", text="hi")
    assert c.is_reply is False
    assert c.like_count is None
