"""MCP tools for playlist management."""

from __future__ import annotations

import json
from typing import Any

from ...core import PlaylistService
from ...core.models import Visibility
from ._helpers import open_client


async def create_playlist(
    title: str, description: str = "", visibility: str = "private"
) -> str:
    """Create a new playlist on the channel.

    Args:
        title: Playlist title.
        description: Playlist description.
        visibility: One of 'public', 'unlisted', 'private'.
    """
    vis = Visibility(visibility.upper())
    async with open_client(authenticated=True) as client:
        pl = await PlaylistService(client).create(title, description=description, visibility=vis)
        return json.dumps(pl.model_dump(mode="json"), indent=2)


async def list_playlists(channel_id: str) -> str:
    """List all playlists belonging to a channel.

    Args:
        channel_id: The channel id (starts with 'UC').
    """
    async with open_client() as client:
        playlists = await PlaylistService(client).list_playlists(channel_id)
        return json.dumps([p.model_dump(mode="json") for p in playlists], indent=2)


async def add_video_to_playlist(playlist_id: str, video_id: str) -> str:
    """Add a video to a playlist.

    Args:
        playlist_id: Target playlist id (starts with 'PL').
        video_id: Video id to add.
    """
    async with open_client(authenticated=True) as client:
        ok = await PlaylistService(client).add_video(playlist_id, video_id)
        return f"Added {video_id} to {playlist_id}: {ok}"


async def remove_video_from_playlist(playlist_id: str, video_id: str) -> str:
    """Remove a video from a playlist.

    Args:
        playlist_id: Target playlist id.
        video_id: Video id to remove.
    """
    async with open_client(authenticated=True) as client:
        ok = await PlaylistService(client).remove_video(playlist_id, video_id)
        return f"Removed {video_id} from {playlist_id}: {ok}"


async def delete_playlist(playlist_id: str) -> str:
    """Delete a playlist.

    Args:
        playlist_id: Playlist id to delete.
    """
    async with open_client(authenticated=True) as client:
        ok = await PlaylistService(client).delete(playlist_id)
        return f"Deleted playlist {playlist_id}: {ok}"


def register(mcp: Any) -> None:
    """Register all playlist tools with the MCP server."""
    for fn in (
        create_playlist,
        list_playlists,
        add_video_to_playlist,
        remove_video_from_playlist,
        delete_playlist,
    ):
        mcp.tool()(fn)


__all__ = [
    "create_playlist",
    "list_playlists",
    "add_video_to_playlist",
    "remove_video_from_playlist",
    "delete_playlist",
    "register",
]
