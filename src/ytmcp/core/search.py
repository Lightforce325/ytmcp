"""Search and list operations (videos, channel uploads)."""

from __future__ import annotations

import logging
from typing import Any

from .client import YouTubeClient
from .exceptions import UpstreamError
from .models import Video

logger = logging.getLogger(__name__)


class SearchService:
    """Search videos and list channel uploads."""

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    async def search(self, query: str, *, limit: int = 20) -> list[Video]:
        """Search YouTube for videos matching ``query``."""
        data = await self.client.innertube("search", {"query": query})
        results: list[Video] = []
        section = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get(
            "primaryContents", {}
        )
        _walk_search(section, results)
        return results[:limit]

    async def list_channel_videos(self, channel_id: str, *, limit: int = 30) -> list[Video]:
        """List a channel's uploaded videos (newest first)."""
        resp = await self.client.get(
            f"https://www.youtube.com/channel/{channel_id}/videos"
        )
        if resp.status_code >= 400:
            raise UpstreamError(f"Channel videos listing failed: {resp.status_code}")
        videos = _parse_channel_videos(resp.text)
        return videos[:limit]


def _walk_search(node: Any, out: list[Video]) -> None:
    if isinstance(node, dict):
        if "videoRenderer" in node:
            vr = node["videoRenderer"]
            out.append(
                Video(
                    video_id=vr.get("videoId", ""),
                    title=_runs(vr.get("title", {}).get("runs", [])),
                    description=_runs(
                        vr.get("detailedMetadataSnippets", [{}])[0]
                        .get("snippetText", {})
                        .get("runs", [])
                    )
                    if vr.get("detailedMetadataSnippets")
                    else "",
                    channel_title=_runs(
                        vr.get("ownerText", {}).get("runs", [])
                    )
                    or _runs(vr.get("longBylineText", {}).get("runs", [])),
                    duration_seconds=_parse_duration(
                        vr.get("lengthText", {}).get("simpleText", "")
                    ),
                    view_count=_to_int(vr.get("viewCountText", {}).get("simpleText")),
                    url=f"https://www.youtube.com/watch?v={vr.get('videoId', '')}",
                )
            )
        for v in node.values():
            _walk_search(v, out)
    elif isinstance(node, list):
        for item in node:
            _walk_search(item, out)


def _parse_channel_videos(html: str) -> list[Video]:
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
    _walk_search(data, videos)
    return videos


def _runs(runs: Any) -> str:
    if isinstance(runs, list):
        return "".join(str(r.get("text", "")) for r in runs if isinstance(r, dict))
    if isinstance(runs, str):
        return runs
    return ""


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    digits = "".join(c for c in str(value) if c.isdigit())
    return int(digits) if digits else None


def _parse_duration(text: str) -> int | None:
    if not text:
        return None
    parts = [int(p) for p in text.split(":") if p.isdigit()]
    if not parts:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds
