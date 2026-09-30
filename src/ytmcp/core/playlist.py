"""Playlist operations: create, list, add/remove videos."""

from __future__ import annotations

import logging
from typing import Any

from .client import YouTubeClient
from .exceptions import UpstreamError
from .models import Playlist, Visibility

logger = logging.getLogger(__name__)

PLAYLIST_MANAGER = "https://studio.youtube.com/youtubei/v1/playlist"
PLAYLIST_EDIT = "https://studio.youtube.com/youtubei/v1/playlist_manager"


class PlaylistService:
    """CRUD operations for playlists."""

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    async def create(
        self,
        title: str,
        *,
        description: str = "",
        visibility: Visibility = Visibility.PRIVATE,
    ) -> Playlist:
        body = {
            "context": self.client.innertube_payload()["context"],
            "title": title,
            "description": description,
            "privacyStatus": visibility.value,
        }
        resp = await self.client.request("POST", PLAYLIST_MANAGER, json=body, mutate=True)
        if resp.status_code >= 400:
            raise UpstreamError(f"Playlist create failed: {resp.status_code}")
        data = _safe_json(resp)
        playlist_id = data.get("playlistId") or data.get("id") or "unknown"
        return Playlist(
            playlist_id=playlist_id, title=title, description=description, visibility=visibility
        )

    async def list_playlists(self, channel_id: str) -> list[Playlist]:
        resp = await self.client.get(
            f"https://www.youtube.com/channel/{channel_id}/playlists"
        )
        if resp.status_code >= 400:
            raise UpstreamError(f"Playlist list failed: {resp.status_code}")
        return _parse_playlists(resp.text)

    async def add_video(self, playlist_id: str, video_id: str) -> bool:
        body = {
            "context": self.client.innertube_payload()["context"],
            "playlistId": playlist_id,
            "videoId": video_id,
        }
        resp = await self.client.request(
            "POST", f"{PLAYLIST_EDIT}/add", json=body, mutate=True
        )
        if resp.status_code >= 400:
            raise UpstreamError(f"Add to playlist failed: {resp.status_code}")
        return True

    async def remove_video(self, playlist_id: str, video_id: str) -> bool:
        body = {
            "context": self.client.innertube_payload()["context"],
            "playlistId": playlist_id,
            "videoId": video_id,
        }
        resp = await self.client.request(
            "POST", f"{PLAYLIST_EDIT}/remove", json=body, mutate=True
        )
        if resp.status_code >= 400:
            raise UpstreamError(f"Remove from playlist failed: {resp.status_code}")
        return True

    async def delete(self, playlist_id: str) -> bool:
        body = {
            "context": self.client.innertube_payload()["context"],
            "playlistId": playlist_id,
        }
        resp = await self.client.request(
            "POST", f"{PLAYLIST_MANAGER}/delete", json=body, mutate=True
        )
        if resp.status_code >= 400:
            raise UpstreamError(f"Playlist delete failed: {resp.status_code}")
        return True


def _safe_json(resp: Any) -> dict[str, Any]:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {}


def _parse_playlists(html: str) -> list[Playlist]:
    import json
    import re

    playlists: list[Playlist] = []
    for match in re.finditer(r'"playlistId":"(PL[\w-]+)".*?"title":\{"runs":\[\{"text":"(.*?)"\}', html):
        playlists.append(Playlist(playlist_id=match.group(1), title=match.group(2)))
    if not playlists:
        m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                playlists = _walk_playlists(data)
            except json.JSONDecodeError:
                pass
    return playlists


def _walk_playlists(node: Any) -> list[Playlist]:
    found: list[Playlist] = []
    if isinstance(node, dict):
        if "lockupViewModel" in node:
            vm = node["lockupViewModel"]
            pid = vm.get("contentId", "")
            if pid.startswith("PL"):
                title = ""
                meta = vm.get("metadata", {}).get("lockupMetadataViewModel", {})
                title = meta.get("title", {}).get("content", "")
                found.append(Playlist(playlist_id=pid, title=title))
        for v in node.values():
            found.extend(_walk_playlists(v))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_playlists(item))
    return found
