"""Video metadata operations: read, update, visibility, delete."""

from __future__ import annotations

import logging
from typing import Any

from .client import YouTubeClient
from .exceptions import NotFoundError, UpstreamError
from .models import Video, Visibility

logger = logging.getLogger(__name__)

VIDEO_MANAGER_URL = "https://studio.youtube.com/youtubei/v1/video_manager/metadata_update"


class MetadataService:
    """Read/write metadata for videos."""

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    async def get_video(self, video_id: str) -> Video:
        """Fetch video info via the player/next InnerTube endpoint."""
        data = await self.client.innertube(
            "next", {"videoId": video_id, "contentCheckOk": True, "racyCheckOk": True}
        )
        details = (
            data.get("contents", {})
            .get("twoColumnWatchNextResults", {})
            .get("results", {})
            .get("results", {})
            .get("contents", [])
        )
        title = ""
        description = ""
        view_count = None
        for item in details:
            primary = item.get("videoPrimaryInfoRenderer")
            if primary:
                title = _runs_text(primary.get("title", {}).get("runs", []))
                view_count = _parse_views(
                    _runs_text(primary.get("viewCount", {}).get("videoViewCountRenderer", {}).get("viewCount", {}).get("runs", []))
                )
            secondary = item.get("videoSecondaryInfoRenderer")
            if secondary:
                description = _runs_text(
                    secondary.get("attributedDescription", {}).get("content", "")
                    if isinstance(secondary.get("attributedDescription", {}).get("content", ""), str)
                    else secondary.get("description", {}).get("runs", [])
                )
        if not title and not description:
            raise NotFoundError(f"Video not found or unavailable: {video_id}")

        return Video(
            video_id=video_id,
            title=title,
            description=description,
            view_count=view_count,
            url=f"https://www.youtube.com/watch?v={video_id}",
        )

    async def update_metadata(
        self,
        video_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        category_id: str | None = None,
        default_language: str | None = None,
    ) -> dict[str, Any]:
        """Update video metadata via Studio. Requires cookie auth."""
        fields: dict[str, Any] = {"videoId": video_id}
        if title is not None:
            fields["title"] = {"newTitle": title[:100]}
        if description is not None:
            fields["description"] = {"newDescription": description, "description": description}
        if tags is not None:
            fields["tags"] = {"newTags": tags}
        if category_id is not None:
            fields["category"] = {"newCategory": {"id": category_id}}
        if default_language is not None:
            fields["language"] = {"newLanguage": default_language}

        resp = await self.client.request(
            "POST", VIDEO_MANAGER_URL, json=self._studio_body(fields), mutate=True
        )
        if resp.status_code >= 400:
            raise UpstreamError(
                f"Metadata update failed: {resp.status_code} {resp.text[:200]}"
            )
        return {"video_id": video_id, "updated_fields": list(fields.keys())}

    async def set_visibility(
        self,
        video_id: str,
        visibility: Visibility,
        *,
        publish_at: str | None = None,
    ) -> dict[str, Any]:
        """Change a video's visibility (public/unlisted/private/scheduled)."""
        payload: dict[str, Any] = {
            "videoId": video_id,
            "privacyStatus": visibility.value,
        }
        if publish_at:
            payload["publishAt"] = publish_at
        resp = await self.client.request(
            "POST", VIDEO_MANAGER_URL, json=self._studio_body(payload), mutate=True
        )
        if resp.status_code >= 400:
            raise UpstreamError(f"Visibility update failed: {resp.status_code}")
        return {"video_id": video_id, "visibility": visibility.value}

    async def delete_video(self, video_id: str) -> bool:
        """Delete a video. Requires cookie auth."""
        url = "https://studio.youtube.com/youtubei/v1/video_manager/delete_video"
        body = {"context": self.client.innertube_payload()["context"], "videoId": video_id}
        resp = await self.client.request("POST", url, json=body, mutate=True)
        if resp.status_code >= 400:
            raise UpstreamError(f"Delete failed: {resp.status_code}")
        return True

    def _studio_body(self, fields: dict[str, Any]) -> dict[str, Any]:
        body = {"context": self.client.innertube_payload()["context"]}
        body.update(fields)
        return body


def _runs_text(runs: Any) -> str:
    if isinstance(runs, str):
        return runs
    if isinstance(runs, list):
        return "".join(str(r.get("text", "")) for r in runs if isinstance(r, dict))
    return ""


def _parse_views(text: str) -> int | None:
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None
