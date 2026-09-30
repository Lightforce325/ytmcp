"""Example: batch-upload videos and organise them into a playlist.

Run with::

    uv run python examples/agent_batch_upload.py

This demonstrates the *core* library directly. Inside an AI agent you would
instead call the equivalent MCP tools (upload_video, create_playlist,
add_video_to_playlist).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ytmcp.core import PlaylistService, UploadController, YouTubeClient
from ytmcp.core.models import Visibility

VIDEOS = [
    # (path, title, description)
    (Path("intro.mp4"), "Introduction", "Welcome to the channel."),
    (Path("demo.mp4"), "Product Demo", "A short walkthrough."),
]


async def main() -> None:
    async with YouTubeClient() as client:
        playlist = await PlaylistService(client).create(
            "Auto Uploads", description="Created by ytmcp", visibility=Visibility.PRIVATE
        )
        print(f"Playlist created: {playlist.playlist_id}")

        controller = UploadController(client)
        for path, title, description in VIDEOS:
            if not path.exists():
                print(f"skip (missing): {path}")
                continue
            result = await controller.upload(
                path,
                title=title,
                description=description,
                visibility=Visibility.PRIVATE,
                tags=["ytmcp", "demo"],
            )
            print(f"uploaded: {result.video_id} -> {result.url}")
            await PlaylistService(client).add_video(playlist.playlist_id, result.video_id)
            print(f"  added to playlist {playlist.playlist_id}")


if __name__ == "__main__":
    asyncio.run(main())
