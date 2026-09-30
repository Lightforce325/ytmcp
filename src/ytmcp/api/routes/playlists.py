"""REST routes for playlists."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ...core import PlaylistService, YouTubeClient
from ...core.models import Visibility

router = APIRouter()


class PlaylistCreate(BaseModel):
    title: str
    description: str = ""
    visibility: str = "private"


class VideoRef(BaseModel):
    video_id: str


@router.post("")
async def create_playlist(body: PlaylistCreate) -> dict[str, Any]:
    async with YouTubeClient() as client:
        pl = await PlaylistService(client).create(
            body.title, description=body.description, visibility=Visibility(body.visibility.upper())
        )
        return pl.model_dump(mode="json")


@router.get("/channel/{channel_id}")
async def list_playlists(channel_id: str) -> list[dict[str, Any]]:
    async with YouTubeClient() as client:
        playlists = await PlaylistService(client).list_playlists(channel_id)
        return [p.model_dump(mode="json") for p in playlists]


@router.post("/{playlist_id}/videos")
async def add_video(playlist_id: str, body: VideoRef) -> dict[str, Any]:
    async with YouTubeClient() as client:
        ok = await PlaylistService(client).add_video(playlist_id, body.video_id)
        return {"ok": ok}


@router.delete("/{playlist_id}/videos/{video_id}")
async def remove_video(playlist_id: str, video_id: str) -> dict[str, Any]:
    async with YouTubeClient() as client:
        ok = await PlaylistService(client).remove_video(playlist_id, video_id)
        return {"ok": ok}


@router.delete("/{playlist_id}")
async def delete_playlist(playlist_id: str) -> dict[str, Any]:
    async with YouTubeClient() as client:
        ok = await PlaylistService(client).delete(playlist_id)
        return {"ok": ok}
