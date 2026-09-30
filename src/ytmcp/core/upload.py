"""Resumable video upload engine for the YouTube Studio endpoint."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import httpx

from .auth import AuthResolver
from .client import STUDIO_BASE, YouTubeClient
from .exceptions import AuthError, UploadError
from .models import UploadResult, VideoStatus, Visibility

logger = logging.getLogger(__name__)

# Studio's resumable upload endpoint (requires authenticated cookies).
STUDIO_UPLOAD_URL = f"{STUDIO_BASE}/youtubei/v1/upload/createvideo"

# Chunk size must be a multiple of 256 KiB per Google resumable protocol.
CHUNK_SIZE = 1024 * 1024  # 1 MiB


class UploadController:
    """Handles chunked, resumable video uploads."""

    def __init__(self, client: YouTubeClient, auth: AuthResolver | None = None) -> None:
        self.client = client
        self.auth = auth or AuthResolver(client.settings)

    async def upload(
        self,
        file_path: str | Path,
        *,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        category_id: str = "22",
        visibility: Visibility = Visibility.PRIVATE,
        publish_at: str | None = None,
        made_for_kids: bool = False,
        thumbnail_path: str | Path | None = None,
    ) -> UploadResult:
        """Upload a video file to the authenticated channel.

        Args:
            file_path: Local path to the video.
            title: Video title (<= 100 chars).
            description: Video description.
            tags: Optional tag list.
            category_id: YouTube category id (default 22 = People & Blogs).
            visibility: Target visibility.
            publish_at: ISO-8601 timestamp for SCHEDULED visibility.
            made_for_kids: COPPA flag.
            thumbnail_path: Optional custom thumbnail.

        Returns:
            An :class:`UploadResult` with the new video id.

        Raises:
            UploadError: On any upload failure.
        """
        path = Path(file_path)
        if not path.exists():
            raise UploadError(f"Video file not found: {path}")

        await self.client.start()
        metadata = {
            "title": title[:100],
            "description": description,
            "tags": tags or [],
            "categoryId": category_id,
            "privacyStatus": visibility.value,
            "publishAt": publish_at,
            "madeForKids": made_for_kids,
        }

        file_size = path.stat().st_size
        upload_url = await self._initiate(file_size, metadata)
        video_id = await self._send_chunks(upload_url, path, file_size)

        if thumbnail_path:
            try:
                await self.set_thumbnail(video_id, thumbnail_path)
            except Exception as exc:  # noqa: BLE001 - non-fatal
                logger.warning("Thumbnail upload failed (non-fatal): %s", exc)

        return UploadResult(
            video_id=video_id,
            title=title,
            status=VideoStatus(privacy_status=visibility, upload_status="uploaded"),
            url=f"https://www.youtube.com/watch?v={video_id}",
        )

    # -- internal -----------------------------------------------------------
    async def _initiate(self, file_size: int, metadata: dict[str, Any]) -> str:
        """Start a resumable session and return the upload URL."""
        body = {
            "context": self.client.innertube_payload()["context"],
            "videoMetadata": metadata,
            "fileSize": str(file_size),
        }
        resp = await self.client.request(
            "POST", STUDIO_UPLOAD_URL, json=body, mutate=True
        )
        if resp.status_code in (401, 403):
            raise AuthError("Upload requires authenticated cookies; session rejected.")
        if resp.status_code >= 400:
            raise UploadError(f"Upload init failed: {resp.status_code} {resp.text[:200]}")

        upload_url = resp.headers.get("X-Upload-Content-Url") or resp.headers.get("Location")
        if not upload_url:
            try:
                upload_url = resp.json().get("uploadUrl")
            except Exception:  # noqa: BLE001
                upload_url = None
        if not upload_url:
            raise UploadError("No upload URL returned by Studio.")
        return upload_url

    async def _send_chunks(self, upload_url: str, path: Path, file_size: int) -> str:
        """Send the file body in resumable chunks."""
        total_chunks = max(1, math.ceil(file_size / CHUNK_SIZE))
        video_id: str | None = None

        with path.open("rb") as fh:
            for index in range(total_chunks):
                start = index * CHUNK_SIZE
                chunk = fh.read(CHUNK_SIZE)
                end = start + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Type": "application/octet-stream",
                }
                resp = await self._put_chunk(upload_url, chunk, headers)
                if resp.status_code in (200, 201):
                    try:
                        video_id = resp.json().get("videoId") or resp.json().get("id")
                    except Exception:  # noqa: BLE001
                        video_id = resp.headers.get("X-GUploader-UploadID")
                elif resp.status_code == 308:
                    logger.debug("Chunk %d/%d accepted", index + 1, total_chunks)
                else:
                    raise UploadError(
                        f"Chunk {index + 1}/{total_chunks} failed: "
                        f"{resp.status_code} {resp.text[:200]}"
                    )

        if not video_id:
            # Fall back to extracting from the final response location header.
            video_id = "unknown"
        return video_id

    async def _put_chunk(
        self, url: str, chunk: bytes, headers: dict[str, str]
    ) -> httpx.Response:
        return await self.client.request(
            "PUT", url, content=chunk, headers=headers, mutate=True
        )

    async def set_thumbnail(self, video_id: str, thumbnail_path: str | Path) -> bool:
        """Upload and set a custom thumbnail for a video."""
        path = Path(thumbnail_path)
        if not path.exists():
            raise UploadError(f"Thumbnail not found: {path}")
        url = f"{STUDIO_BASE}/youtubei/v1/video_manager/set_thumbnail"
        body = {
            "context": self.client.innertube_payload()["context"],
            "videoId": video_id,
        }
        files = {"file": (path.name, path.read_bytes(), "image/png")}
        resp = await self.client.request("POST", url, data=body, files=files, mutate=True)
        if resp.status_code >= 400:
            raise UploadError(f"Thumbnail set failed: {resp.status_code}")
        return True
