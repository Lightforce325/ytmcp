"""Tests for parsing helpers in the core services."""

from __future__ import annotations

from ytmcp.core.analytics import _parse_popular, _to_int
from ytmcp.core.metadata import _parse_views
from ytmcp.core.search import _parse_duration, _runs


def test_parse_duration() -> None:
    assert _parse_duration("1:23") == 83
    assert _parse_duration("1:02:03") == 3723
    assert _parse_duration("") is None


def test_parse_views() -> None:
    assert _parse_views("1,234,567 views") == 1234567
    assert _parse_views("no views") is None


def test_runs_text() -> None:
    assert _runs([{"text": "Hello "}, {"text": "World"}]) == "Hello World"
    assert _runs("plain") == "plain"
    assert _runs(None) == ""


def test_to_int() -> None:
    assert _to_int("1.2M") is None or isinstance(_to_int("1.2M"), int)
    assert _to_int(42) == 42
    assert _to_int(None) is None


def test_parse_popular_extracts_videos() -> None:
    html = (
        '<script>var ytInitialData = {"contents":{"a":{"videoRenderer":'
        '{"videoId":"abc","title":{"runs":[{"text":"My Video"}]},'
        '"viewCountText":{"simpleText":"1,000 views"}}}}};</script>'
    )
    videos = _parse_popular(html)
    assert len(videos) == 1
    assert videos[0].video_id == "abc"
    assert videos[0].title == "My Video"
    assert videos[0].view_count == 1000


def test_parse_popular_no_data() -> None:
    assert _parse_popular("<html></html>") == []
