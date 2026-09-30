"""MCP tools for channel info, branding, and analytics."""

from __future__ import annotations

import json
from typing import Any

from ...core import AnalyticsService, ChannelService
from ._helpers import open_client


async def get_channel_info(channel_id: str | None = None) -> str:
    """Get information about a channel (title, description, banner).

    Args:
        channel_id: Channel id; if omitted, uses the authenticated channel.
    """
    async with open_client(authenticated=channel_id is None) as client:
        info = await ChannelService(client).get_info(channel_id)
        return json.dumps(info.model_dump(mode="json"), indent=2)


async def get_subscriber_count(channel_id: str | None = None) -> str:
    """Get the subscriber count of a channel.

    Args:
        channel_id: Channel id; if omitted, uses the authenticated channel.
    """
    async with open_client(authenticated=channel_id is None) as client:
        count = await ChannelService(client).get_subscriber_count(channel_id)
        return f"Subscribers: {count if count is not None else 'unknown'}"


async def update_channel_branding(
    banner_path: str | None = None,
    avatar_path: str | None = None,
    links: list[dict[str, str]] | None = None,
) -> str:
    """Update channel branding: banner, avatar, and profile links.

    Args:
        banner_path: Local path to a new banner image.
        avatar_path: Local path to a new avatar image.
        links: List of {'title': ..., 'url': ...} profile links.
    """
    async with open_client(authenticated=True) as client:
        result = await ChannelService(client).update_branding(
            banner_path=banner_path, avatar_path=avatar_path, links=links
        )
        return json.dumps(result, indent=2)


async def get_analytics(channel_id: str, period_days: int = 28) -> str:
    """Get channel analytics (views, watch-time, subscribers gained/lost).

    Args:
        channel_id: Channel id.
        period_days: Reporting window in days (default 28).
    """
    async with open_client(authenticated=True) as client:
        analytics = await AnalyticsService(client).get_channel_analytics(
            channel_id, period_days=period_days
        )
        return json.dumps(analytics.model_dump(mode="json"), indent=2)


async def get_top_videos(channel_id: str, limit: int = 10) -> str:
    """Get a channel's most-viewed videos.

    Args:
        channel_id: Channel id.
        limit: Maximum number of videos to return.
    """
    async with open_client() as client:
        videos = await AnalyticsService(client).get_top_videos(channel_id, limit=limit)
        return json.dumps([v.model_dump(mode="json") for v in videos], indent=2)


def register(mcp: Any) -> None:
    """Register all channel/analytics tools with the MCP server."""
    for fn in (
        get_channel_info,
        get_subscriber_count,
        update_channel_branding,
        get_analytics,
        get_top_videos,
    ):
        mcp.tool()(fn)


__all__ = [
    "get_channel_info",
    "get_subscriber_count",
    "update_channel_branding",
    "get_analytics",
    "get_top_videos",
    "register",
]
