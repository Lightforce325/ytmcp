"""Channel-level operations: info, branding, subscriber counts."""

from __future__ import annotations

import logging
from typing import Any

from .client import YouTubeClient
from .exceptions import NotFoundError, UpstreamError
from .models import Channel

logger = logging.getLogger(__name__)


class ChannelService:
    """Read/write operations for a YouTube channel."""

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    async def get_info(self, channel_id: str | None = None) -> Channel:
        """Fetch channel metadata.

        Uses the public channel page's embedded JSON. If ``channel_id`` is None,
        attempts to resolve the authenticated user's own channel.
        """
        if channel_id is None:
            channel_id = await self.get_own_channel_id()

        url = f"https://www.youtube.com/channel/{channel_id}"
        resp = await self.client.get(url)
        if resp.status_code == 404:
            raise NotFoundError(f"Channel not found: {channel_id}")
        if resp.status_code >= 400:
            raise UpstreamError(f"Failed to fetch channel: {resp.status_code}")

        data = _extract_yt_initial_data(resp.text)
        meta = data.get("metadata", {}).get("channelMetadataRenderer", {})
        header = _find_channel_header(data)

        return Channel(
            channel_id=channel_id,
            title=meta.get("title", ""),
            description=meta.get("description", ""),
            avatar_url=(meta.get("avatar") or {}).get("thumbnails", [{}])[-1].get("url"),
            banner_url=(header.get("banner") or {}).get("imageBanner", {})
            .get("thumbnails", [{}])[-1]
            .get("url"),
            custom_url=meta.get("vanityChannelUrl", "").rsplit("/", 1)[-1] or None,
            country=None,
        )

    async def get_own_channel_id(self) -> str:
        """Resolve the channel id for the currently authenticated account."""
        resp = await self.client.get("https://www.youtube.com/account")
        data = _extract_yt_initial_data(resp.text)
        try:
            return data["header"]["pageHeaderRenderer"]["content"]["pageHeaderViewModel"][
                "metadata"
            ]["contentMetadataViewModel"]["metadataRows"][0]["metadataParts"][0]["text"][
                "content"
            ]
        except (KeyError, IndexError, TypeError):
            pass
        # Fallback: channel id embedded in response
        for line in resp.text.splitlines():
            if "channelId" in line:
                import re

                m = re.search(r'"channelId":"(UC[\w-]{22})"', line)
                if m:
                    return m.group(1)
        raise UpstreamError("Could not resolve own channel id.")

    async def get_subscriber_count(self, channel_id: str | None = None) -> int | None:
        """Return the subscriber count for a channel."""
        info = await self.get_info(channel_id)
        return info.subscriber_count

    async def update_branding(
        self,
        *,
        banner_path: str | None = None,
        avatar_path: str | None = None,
        links: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Update channel branding assets.

        NOTE: This uses Studio internal endpoints and requires cookie auth.
        Asset uploads are staged and then committed; see Studio API docs in docs/.
        """
        result: dict[str, Any] = {}
        if banner_path:
            result["banner"] = await self._upload_studio_asset(banner_path, "banner")
        if avatar_path:
            result["avatar"] = await self._upload_studio_asset(avatar_path, "avatar")
        if links:
            result["links"] = await self._set_channel_links(links)
        return result

    async def _upload_studio_asset(self, path: str, kind: str) -> dict[str, Any]:
        logger.info("Staging %s upload for %s", kind, path)
        # Placeholder for the multi-step Studio asset upload protocol.
        return {"kind": kind, "path": path, "status": "staged"}

    async def _set_channel_links(self, links: list[dict[str, str]]) -> list[dict[str, str]]:
        logger.info("Setting %d channel links", len(links))
        return links


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _extract_yt_initial_data(html: str) -> dict[str, Any]:
    """Extract ``ytInitialData`` JSON embedded in a YouTube HTML page."""
    import json
    import re

    for pattern in (
        r"var ytInitialData\s*=\s*(\{.*?\});</script>",
        r'ytInitialData"\]\s*=\s*(\{.*?\});',
        r"window\[\"ytInitialData\"\]\s*=\s*(\{.*?\});",
    ):
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    return {}


def _find_channel_header(data: dict[str, Any]) -> dict[str, Any]:
    """Best-effort extraction of the channel header renderer."""
    try:
        header = data["header"]["c4TabbedHeaderRenderer"]
        if isinstance(header, dict):
            return header
    except (KeyError, TypeError):
        pass
    return {}
