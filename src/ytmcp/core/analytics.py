"""Channel analytics scraping (views, watch-time, growth, top videos)."""

from __future__ import annotations

import logging
from typing import Any

from .client import YouTubeClient
from .exceptions import UpstreamError
from .models import Analytics, Video

logger = logging.getLogger(__name__)

ANALYTICS_URL = "https://studio.youtube.com/youtubei/v1/analytics"


class AnalyticsService:
    """Fetch channel analytics data.

    Prefers the public InnerTube analytics endpoint; falls back to
    Studio scraping when cookie auth is available.
    """

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    async def get_channel_analytics(
        self, channel_id: str, *, period_days: int = 28
    ) -> Analytics:
        """Return aggregated analytics for the given period."""
        body = {
            "context": self.client.innertube_payload()["context"],
            "channelId": channel_id,
            "periodDays": period_days,
        }
        try:
            data = await self.client.request("POST", ANALYTICS_URL, json=body)
            if data.status_code < 400:
                parsed = _parse_analytics(data.json(), channel_id, period_days)
                if parsed.views is not None:
                    return parsed
        except Exception as exc:  # noqa: BLE001
            logger.debug("Analytics endpoint failed, trying fallback: %s", exc)

        # Fallback: scrape the Studio analytics dashboard (requires cookies).
        return await self._fallback(channel_id, period_days)

    async def _fallback(self, channel_id: str, period_days: int) -> Analytics:
        try:
            resp = await self.client.get(
                f"https://studio.youtube.com/channel/{channel_id}/analytics/tab-overview"
            )
            if resp.status_code >= 400:
                raise UpstreamError(f"Analytics fallback failed: {resp.status_code}")
            return _scrape_analytics(resp.text, channel_id, period_days)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analytics unavailable: %s", exc)
            return Analytics(channel_id=channel_id, period_days=period_days)

    async def get_top_videos(self, channel_id: str, *, limit: int = 10) -> list[Video]:
        """Return the channel's most-viewed videos (popular tab)."""
        resp = await self.client.get(
            f"https://www.youtube.com/channel/{channel_id}/videos?view=0&sort=p&flow=grid"
        )
        if resp.status_code >= 400:
            return []
        return _parse_popular(resp.text)[:limit]


def _parse_analytics(data: dict[str, Any], channel_id: str, period_days: int) -> Analytics:
    metrics: dict[str, Any] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("views", "watchTimeMinutes", "subscribersGained", "subscribersLost"):
                if key in node and isinstance(node[key], (int, float, str)):
                    metrics.setdefault(key, node[key])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return Analytics(
        channel_id=channel_id,
        period_days=period_days,
        views=_to_int(metrics.get("views")),
        watch_time_minutes=_to_float(metrics.get("watchTimeMinutes")),
        subscribers_gained=_to_int(metrics.get("subscribersGained")),
        subscribers_lost=_to_int(metrics.get("subscribersLost")),
    )


def _scrape_analytics(html: str, channel_id: str, period_days: int) -> Analytics:
    import json
    import re

    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", html, re.DOTALL)
    if not m:
        return Analytics(channel_id=channel_id, period_days=period_days)
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return Analytics(channel_id=channel_id, period_days=period_days)
    return _parse_analytics(data, channel_id, period_days)


def _parse_popular(html: str) -> list[Video]:
    import json
    import re

    videos: list[Video] = []
    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", html, re.DOTALL)
    if not m:
        return videos
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return videos

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "videoRenderer" in node:
                vr = node["videoRenderer"]
                videos.append(
                    Video(
                        video_id=vr.get("videoId", ""),
                        title=_runs(vr.get("title", {}).get("runs", [])),
                        view_count=_to_int(vr.get("viewCountText", {}).get("simpleText")),
                        url=f"https://www.youtube.com/watch?v={vr.get('videoId', '')}",
                    )
                )
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return videos


def _runs(runs: Any) -> str:
    if isinstance(runs, list):
        return "".join(str(r.get("text", "")) for r in runs if isinstance(r, dict))
    return ""


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = "".join(c for c in str(value) if c.isdigit())
    return int(digits) if digits else None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
