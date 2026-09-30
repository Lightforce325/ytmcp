"""MCP tools for video read/write operations."""

from __future__ import annotations

import json
from typing import Any

from ...core import MetadataService, SearchService, UploadController
from ...core.models import Visibility
from ._helpers import open_client


async def upload_video(
    file_path: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    visibility: str = "private",
    category_id: str = "22",
    publish_at: str | None = None,
    made_for_kids: bool = False,
    thumbnail_path: str | None = None,
) -> str:
    """Upload a video file to the authenticated YouTube channel.

    Args:
        file_path: Absolute path to the video file on the local machine.
        title: Video title (max 100 chars).
        description: Video description text.
        tags: Optional list of tags/keywords.
        visibility: One of 'public', 'unlisted', 'private', 'scheduled'.
        category_id: YouTube category id (default '22' = People & Blogs).
        publish_at: ISO-8601 datetime; required when visibility='scheduled'.
        made_for_kids: COPPA compliance flag.
        thumbnail_path: Optional path to a custom thumbnail image.
    """
    vis = Visibility(visibility.upper())
    async with open_client(authenticated=True) as client:
        controller = UploadController(client)
        result = await controller.upload(
            file_path,
            title=title,
            description=description,
            tags=tags,
            visibility=vis,
            category_id=category_id,
            publish_at=publish_at,
            made_for_kids=made_for_kids,
            thumbnail_path=thumbnail_path,
        )
        return json.dumps(result.model_dump(mode="json"), indent=2)


async def update_video_metadata(
    video_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    category_id: str | None = None,
    default_language: str | None = None,
) -> str:
    """Update the metadata of an existing video.

    Args:
        video_id: The YouTube video id to edit.
        title: New title (optional).
        description: New description (optional).
        tags: New tag list (optional).
        category_id: New category id (optional).
        default_language: New default language code, e.g. 'en' (optional).
    """
    async with open_client(authenticated=True) as client:
        service = MetadataService(client)
        result = await service.update_metadata(
            video_id,
            title=title,
            description=description,
            tags=tags,
            category_id=category_id,
            default_language=default_language,
        )
        return json.dumps(result, indent=2)


async def set_video_visibility(
    video_id: str, visibility: str, publish_at: str | None = None
) -> str:
    """Change a video's visibility.

    Args:
        video_id: The YouTube video id.
        visibility: One of 'public', 'unlisted', 'private', 'scheduled'.
        publish_at: ISO-8601 datetime; required for 'scheduled'.
    """
    vis = Visibility(visibility.upper())
    async with open_client(authenticated=True) as client:
        result = await MetadataService(client).set_visibility(
            video_id, vis, publish_at=publish_at
        )
        return json.dumps(result, indent=2)


async def delete_video(video_id: str) -> str:
    """Permanently delete a video from the channel.

    Args:
        video_id: The YouTube video id to delete.
    """
    async with open_client(authenticated=True) as client:
        ok = await MetadataService(client).delete_video(video_id)
        return f"Deleted video {video_id}: {ok}"


async def get_video_info(video_id: str) -> str:
    """Get detailed information about a video (title, views, description).

    Args:
        video_id: The YouTube video id.
    """
    async with open_client() as client:
        video = await MetadataService(client).get_video(video_id)
        return json.dumps(video.model_dump(mode="json"), indent=2)


async def search_videos(query: str, limit: int = 20) -> str:
    """Search YouTube for videos.

    Args:
        query: Search keywords.
        limit: Maximum number of results (default 20).
    """
    async with open_client() as client:
        videos = await SearchService(client).search(query, limit=limit)
        return json.dumps([v.model_dump(mode="json") for v in videos], indent=2)


async def list_channel_videos(channel_id: str, limit: int = 30) -> str:
    """List a channel's uploaded videos (newest first).

    Args:
        channel_id: The channel id (starts with 'UC').
        limit: Maximum number of videos to return.
    """
    async with open_client() as client:
        videos = await SearchService(client).list_channel_videos(channel_id, limit=limit)
        return json.dumps([v.model_dump(mode="json") for v in videos], indent=2)


def register(mcp: Any) -> None:
    """Register all video tools with the MCP server."""
    for fn in (
        upload_video,
        update_video_metadata,
        set_video_visibility,
        delete_video,
        get_video_info,
        search_videos,
        list_channel_videos,
    ):
        mcp.tool()(fn)


__all__ = [
    "upload_video",
    "update_video_metadata",
    "set_video_visibility",
    "delete_video",
    "get_video_info",
    "search_videos",
    "list_channel_videos",
    "register",
]
