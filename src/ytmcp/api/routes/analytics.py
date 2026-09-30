"""REST routes for analytics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...core import AnalyticsService, YouTubeClient

router = APIRouter()


@router.get("/{channel_id}")
async def channel_analytics(channel_id: str, period_days: int = 28) -> dict[str, Any]:
    async with YouTubeClient() as client:
        analytics = await AnalyticsService(client).get_channel_analytics(
            channel_id, period_days=period_days
        )
        return analytics.model_dump(mode="json")


@router.get("/{channel_id}/top-videos")
async def top_videos(channel_id: str, limit: int = 10) -> list[dict[str, Any]]:
    async with YouTubeClient() as client:
        videos = await AnalyticsService(client).get_top_videos(channel_id, limit=limit)
        return [v.model_dump(mode="json") for v in videos]
