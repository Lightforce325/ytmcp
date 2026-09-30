"""Shared helpers and session management for MCP tools."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ...config import Settings, get_settings
from ...core import AuthResolver, YouTubeClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_client(
    *, authenticated: bool = False, settings: Settings | None = None
) -> AsyncIterator[YouTubeClient]:
    """Open a :class:`YouTubeClient`, optionally resolving auth.

    Args:
        authenticated: When True, apply credentials (raises if unavailable).
        settings: Optional settings override.
    """
    settings = settings or get_settings()
    client = YouTubeClient(settings)
    await client.start()
    try:
        if authenticated:
            await AuthResolver(settings).apply(client)
        yield client
    finally:
        await client.close()


def text_result(message: str) -> str:
    """Standardise a plain-text tool result."""
    return message
