"""Tests for core/analytics.py: parsing helpers + AnalyticsService flows."""

from __future__ import annotations

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core.analytics import (
    ANALYTICS_URL,
    AnalyticsService,
    _parse_analytics,
    _parse_popular,
    _runs,
    _scrape_analytics,
    _to_float,
    _to_int,
)
from ytmcp.core.client import YouTubeClient

# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
CHANNEL = "UC_test_channel"
POPULAR_URL = (
    f"https://www.youtube.com/channel/{CHANNEL}/videos?view=0&sort=p&flow=grid"
)
FALLBACK_URL = (
    f"https://studio.youtube.com/channel/{CHANNEL}/analytics/tab-overview"
)


def _analytics_html(payload: dict) -> str:
    """Wrap a ytInitialData payload in the exact shape the regex expects."""
    import json

    return f"<html><script>var ytInitialData = {json.dumps(payload)};</script></html>"


def _metrics_payload() -> dict:
    """Nested structure mirroring the real analytics response."""
    return {
        "contents": {
            "cards": [
                {
                    "cardRenderer": {
                        "metrics": {
                            "views": 12345,
                            "watchTimeMinutes": 9876.5,
                        },
                        "growth": {
                            "subscribersGained": "42",
                            "subscribersLost": 7,
                        },
                    }
                }
            ]
        }
    }


# --------------------------------------------------------------------------- #
# _runs (used by _parse_popular)
# --------------------------------------------------------------------------- #
def test_runs_joins_text_fragments() -> None:
    assert _runs([{"text": "A"}, {"text": "B"}, {"text": "C"}]) == "ABC"


def test_runs_ignores_non_dict_items() -> None:
    assert _runs([{"text": "x"}, "nope", 5, None]) == "x"


def test_runs_returns_empty_for_non_list() -> None:
    assert _runs(None) == ""
    assert _runs("plain") == ""
    assert _runs(123) == ""


def test_runs_handles_missing_text_key() -> None:
    assert _runs([{"other": 1}, {"text": "y"}]) == "y"


# --------------------------------------------------------------------------- #
# _to_int / _to_float
# --------------------------------------------------------------------------- #
def test_to_int_none_and_empty() -> None:
    assert _to_int(None) is None
    assert _to_int("") is None
    assert _to_int("no digits here") is None


def test_to_int_numeric_types() -> None:
    assert _to_int(42) == 42
    assert _to_int(42.9) == 42
    assert _to_int(0) == 0


def test_to_int_strips_non_digits() -> None:
    assert _to_int("1,234 views") == 1234
    assert _to_int("1.2M") == 12
    assert _to_int("  99  ") == 99


def test_to_float_various() -> None:
    assert _to_float(None) is None
    assert _to_float("1.5") == 1.5
    assert _to_float(3) == 3.0
    assert _to_float(2.25) == 2.25
    assert _to_float("abc") is None
    assert _to_float([1]) is None


# --------------------------------------------------------------------------- #
# _parse_analytics
# --------------------------------------------------------------------------- #
def test_parse_analytics_extracts_nested_metrics() -> None:
    parsed = _parse_analytics(_metrics_payload(), CHANNEL, 28)
    assert parsed.channel_id == CHANNEL
    assert parsed.period_days == 28
    assert parsed.views == 12345
    assert parsed.watch_time_minutes == 9876.5
    assert parsed.subscribers_gained == 42
    assert parsed.subscribers_lost == 7


def test_parse_analytics_missing_values_are_none() -> None:
    parsed = _parse_analytics({"unrelated": [{"x": 1}]}, CHANNEL, 7)
    assert parsed.views is None
    assert parsed.watch_time_minutes is None
    assert parsed.subscribers_gained is None
    assert parsed.subscribers_lost is None
    assert parsed.period_days == 7


def test_parse_analytics_first_match_wins() -> None:
    data = {"a": {"views": 1}, "b": {"views": 2}}
    assert _parse_analytics(data, CHANNEL, 28).views == 1


def test_parse_analytics_ignores_non_scalar_metrics() -> None:
    data = {"views": {"nested": 5}, "watchTimeMinutes": None}
    parsed = _parse_analytics(data, CHANNEL, 28)
    assert parsed.views is None
    assert parsed.watch_time_minutes is None


# --------------------------------------------------------------------------- #
# _scrape_analytics
# --------------------------------------------------------------------------- #
def test_scrape_analytics_valid_html() -> None:
    parsed = _scrape_analytics(_analytics_html(_metrics_payload()), CHANNEL, 28)
    assert parsed.views == 12345
    assert parsed.subscribers_gained == 42


def test_scrape_analytics_without_script_returns_default() -> None:
    parsed = _scrape_analytics("<html><body>nothing</body></html>", CHANNEL, 28)
    assert parsed.channel_id == CHANNEL
    assert parsed.period_days == 28
    assert parsed.views is None


def test_scrape_analytics_invalid_json_returns_default() -> None:
    html = "<script>var ytInitialData = {not valid json};</script>"
    parsed = _scrape_analytics(html, CHANNEL, 28)
    assert parsed.channel_id == CHANNEL
    assert parsed.views is None


# --------------------------------------------------------------------------- #
# _parse_popular
# --------------------------------------------------------------------------- #
def test_parse_popular_no_script_returns_empty() -> None:
    assert _parse_popular("<html></html>") == []


def test_parse_popular_invalid_json_returns_empty() -> None:
    html = "<script>var ytInitialData = {broken};</script>"
    assert _parse_popular(html) == []


def test_parse_popular_multiple_videos_in_order() -> None:
    html = _analytics_html(
        {
            "contents": [
                {
                    "videoRenderer": {
                        "videoId": "v1",
                        "title": {"runs": [{"text": "First"}]},
                        "viewCountText": {"simpleText": "3,000 views"},
                    }
                },
                {
                    "videoRenderer": {
                        "videoId": "v2",
                        "title": {"runs": [{"text": "Second"}]},
                        "viewCountText": {"simpleText": "1,200 views"},
                    }
                },
            ]
        }
    )
    videos = _parse_popular(html)
    assert [v.video_id for v in videos] == ["v1", "v2"]
    assert videos[0].view_count == 3000
    assert videos[0].url == "https://www.youtube.com/watch?v=v1"


def test_parse_popular_missing_fields_defaults() -> None:
    html = _analytics_html({"videoRenderer": {}})
    videos = _parse_popular(html)
    assert len(videos) == 1
    assert videos[0].video_id == ""
    assert videos[0].title == ""
    assert videos[0].view_count is None


# --------------------------------------------------------------------------- #
# AnalyticsService.get_channel_analytics
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_get_channel_analytics_primary_endpoint(settings: Settings) -> None:
    route = respx.post(ANALYTICS_URL).mock(
        return_value=httpx.Response(200, json=_metrics_payload())
    )
    async with YouTubeClient(settings) as client:
        analytics = await AnalyticsService(client).get_channel_analytics(
            CHANNEL, period_days=30
        )

    assert route.called
    assert analytics.channel_id == CHANNEL
    assert analytics.period_days == 30
    assert analytics.views == 12345
    assert analytics.watch_time_minutes == 9876.5
    assert analytics.subscribers_gained == 42
    assert analytics.subscribers_lost == 7

    body = route.calls.last.request.content.decode()
    assert CHANNEL in body
    assert '"periodDays": 30' in body or '"periodDays":30' in body


@pytest.mark.asyncio
@respx.mock
async def test_get_channel_analytics_default_period_is_28(settings: Settings) -> None:
    respx.post(ANALYTICS_URL).mock(
        return_value=httpx.Response(200, json=_metrics_payload())
    )
    async with YouTubeClient(settings) as client:
        analytics = await AnalyticsService(client).get_channel_analytics(CHANNEL)
    assert analytics.period_days == 28


@pytest.mark.asyncio
@respx.mock
async def test_get_channel_analytics_falls_back_on_4xx(settings: Settings) -> None:
    respx.post(ANALYTICS_URL).mock(return_value=httpx.Response(403, text="denied"))
    respx.get(FALLBACK_URL).mock(
        return_value=httpx.Response(200, text=_analytics_html(_metrics_payload()))
    )
    async with YouTubeClient(settings) as client:
        analytics = await AnalyticsService(client).get_channel_analytics(
            CHANNEL, period_days=7
        )
    assert analytics.views == 12345
    assert analytics.period_days == 7


@pytest.mark.asyncio
@respx.mock
async def test_get_channel_analytics_falls_back_when_no_views(settings: Settings) -> None:
    """200 with a body lacking metrics must not short-circuit the fallback."""
    respx.post(ANALYTICS_URL).mock(
        return_value=httpx.Response(200, json={"unrelated": True})
    )
    respx.get(FALLBACK_URL).mock(
        return_value=httpx.Response(200, text=_analytics_html(_metrics_payload()))
    )
    async with YouTubeClient(settings) as client:
        analytics = await AnalyticsService(client).get_channel_analytics(CHANNEL)
    assert analytics.views == 12345


@pytest.mark.asyncio
@respx.mock
async def test_get_channel_analytics_fallback_error_returns_default(
    settings: Settings,
) -> None:
    respx.post(ANALYTICS_URL).mock(return_value=httpx.Response(401))
    respx.get(FALLBACK_URL).mock(return_value=httpx.Response(500, text="boom"))
    async with YouTubeClient(settings) as client:
        analytics = await AnalyticsService(client).get_channel_analytics(
            CHANNEL, period_days=14
        )
    assert analytics.channel_id == CHANNEL
    assert analytics.period_days == 14
    assert analytics.views is None


@pytest.mark.asyncio
@respx.mock
async def test_get_channel_analytics_fallback_network_error_returns_default(
    settings: Settings,
) -> None:
    """A transport-level failure in the fallback path is swallowed too."""
    respx.post(ANALYTICS_URL).mock(return_value=httpx.Response(403))
    respx.get(FALLBACK_URL).mock(side_effect=httpx.ConnectError("no route"))
    async with YouTubeClient(settings) as client:
        analytics = await AnalyticsService(client).get_channel_analytics(
            CHANNEL, period_days=3
        )
    assert analytics.channel_id == CHANNEL
    assert analytics.period_days == 3
    assert analytics.views is None


@pytest.mark.asyncio
@respx.mock
async def test_get_channel_analytics_primary_endpoint_error_falls_back(
    settings: Settings,
) -> None:
    """A non-HTTP error from the primary endpoint must not propagate."""
    respx.post(ANALYTICS_URL).mock(side_effect=httpx.ConnectError("boom"))
    respx.get(FALLBACK_URL).mock(
        return_value=httpx.Response(200, text=_analytics_html(_metrics_payload()))
    )
    async with YouTubeClient(settings) as client:
        analytics = await AnalyticsService(client).get_channel_analytics(CHANNEL)
    assert analytics.views == 12345

@pytest.mark.asyncio
@respx.mock
async def test_get_channel_analytics_fallback_4xx_returns_default_and_warns(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """The Studio fallback returning a 4xx hits the explicit ``raise`` path.

    A 4xx is *returned* by the client (not retried), so ``_fallback`` sees
    ``resp.status_code >= 400``, raises ``UpstreamError``, and the surrounding
    ``try/except`` swallows it — yielding the default ``Analytics`` and logging
    a warning. This is the only way to execute the ``raise`` statement itself
    (a 5xx would raise inside the client's retry loop instead).
    """
    respx.post(ANALYTICS_URL).mock(return_value=httpx.Response(403, text="denied"))
    fallback_route = respx.get(FALLBACK_URL).mock(
        return_value=httpx.Response(404, text="not found")
    )
    with caplog.at_level("WARNING", logger="ytmcp.core.analytics"):
        async with YouTubeClient(settings) as client:
            analytics = await AnalyticsService(client).get_channel_analytics(
                CHANNEL, period_days=21
            )

    assert fallback_route.called
    assert analytics.channel_id == CHANNEL
    assert analytics.period_days == 21
    assert analytics.views is None
    assert any("Analytics unavailable" in r.message for r in caplog.records)
    assert any("Analytics fallback failed: 404" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# AnalyticsService.get_top_videos
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_get_top_videos_returns_videos(settings: Settings) -> None:
    html = _analytics_html(
        {
            "contents": {
                "videoRenderer": {
                    "videoId": "top1",
                    "title": {"runs": [{"text": "Top One"}]},
                    "viewCountText": {"simpleText": "9,000 views"},
                }
            }
        }
    )
    respx.get(POPULAR_URL).mock(return_value=httpx.Response(200, text=html))
    async with YouTubeClient(settings) as client:
        videos = await AnalyticsService(client).get_top_videos(CHANNEL)
    assert len(videos) == 1
    assert videos[0].video_id == "top1"
    assert videos[0].title == "Top One"


@pytest.mark.asyncio
@respx.mock
async def test_get_top_videos_applies_limit(settings: Settings) -> None:
    contents = [
        {
            "videoRenderer": {
                "videoId": f"v{i}",
                "title": {"runs": [{"text": f"Video {i}"}]},
            }
        }
        for i in range(5)
    ]
    respx.get(POPULAR_URL).mock(
        return_value=httpx.Response(200, text=_analytics_html({"contents": contents}))
    )
    async with YouTubeClient(settings) as client:
        videos = await AnalyticsService(client).get_top_videos(CHANNEL, limit=2)
    assert [v.video_id for v in videos] == ["v0", "v1"]


@pytest.mark.asyncio
@respx.mock
async def test_get_top_videos_returns_empty_on_4xx(settings: Settings) -> None:
    respx.get(POPULAR_URL).mock(return_value=httpx.Response(404, text="nope"))
    async with YouTubeClient(settings) as client:
        videos = await AnalyticsService(client).get_top_videos(CHANNEL)
    assert videos == []
