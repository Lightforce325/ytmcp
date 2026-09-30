"""REST routes for video operations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core import MetadataService, SearchService, YouTubeClient
from ...core.models import Visibility

router = APIRouter()


class MetadataUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    category_id: str | None = None
    default_language: str | None = None


class VisibilityUpdate(BaseModel):
    visibility: str
    publish_at: str | None = None


@router.get("/search")
async def search(q: str, limit: int = 20) -> list[dict[str, Any]]:
    async with YouTubeClient() as client:
        videos = await SearchService(client).search(q, limit=limit)
        return [v.model_dump(mode="json") for v in videos]


@router.get("/{video_id}")
async def get_video(video_id: str) -> dict[str, Any]:
    async with YouTubeClient() as client:
        try:
            video = await MetadataService(client).get_video(video_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return video.model_dump(mode="json")


@router.get("/channel/{channel_id}")
async def list_channel_videos(channel_id: str, limit: int = 30) -> list[dict[str, Any]]:
    async with YouTubeClient() as client:
        videos = await SearchService(client).list_channel_videos(channel_id, limit=limit)
        return [v.model_dump(mode="json") for v in videos]


@router.patch("/{video_id}/metadata")
async def update_metadata(video_id: str, body: MetadataUpdate) -> dict[str, Any]:
    async with YouTubeClient() as client:
        return await MetadataService(client).update_metadata(
            video_id,
            title=body.title,
            description=body.description,
            tags=body.tags,
            category_id=body.category_id,
            default_language=body.default_language,
        )


@router.patch("/{video_id}/visibility")
async def set_visibility(video_id: str, body: VisibilityUpdate) -> dict[str, Any]:
    async with YouTubeClient() as client:
        return await MetadataService(client).set_visibility(
            video_id, Visibility(body.visibility.upper()), publish_at=body.publish_at
        )


@router.delete("/{video_id}")
async def delete_video(video_id: str) -> dict[str, str]:
    async with YouTubeClient() as client:
        await MetadataService(client).delete_video(video_id)
        return {"deleted": video_id}
