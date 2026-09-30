"""REST routes for channel info and branding."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ...core import ChannelService, YouTubeClient

router = APIRouter()


class BrandingUpdate(BaseModel):
    banner_path: str | None = None
    avatar_path: str | None = None
    links: list[dict[str, str]] | None = None


@router.get("/{channel_id}")
async def get_channel(channel_id: str) -> dict[str, Any]:
    async with YouTubeClient() as client:
        info = await ChannelService(client).get_info(channel_id)
        return info.model_dump(mode="json")


@router.get("/{channel_id}/subscribers")
async def subscribers(channel_id: str) -> dict[str, Any]:
    async with YouTubeClient() as client:
        count = await ChannelService(client).get_subscriber_count(channel_id)
        return {"channel_id": channel_id, "subscribers": count}


@router.patch("/{channel_id}/branding")
async def update_branding(channel_id: str, body: BrandingUpdate) -> dict[str, Any]:
    async with YouTubeClient() as client:
        return await ChannelService(client).update_branding(
            banner_path=body.banner_path, avatar_path=body.avatar_path, links=body.links
        )
