"""Core package: unofficial YouTube client services."""

from __future__ import annotations

from .analytics import AnalyticsService
from .auth import AuthResolver, load_cookie_file
from .channel import ChannelService
from .client import YouTubeClient
from .comments import CommentService
from .exceptions import (
    AuthError,
    AuthRequiredError,
    NotFoundError,
    RateLimitError,
    UploadError,
    UpstreamError,
    YtmcpError,
)
from .metadata import MetadataService
from .models import (
    Analytics,
    AuthContext,
    Channel,
    Comment,
    Playlist,
    UploadResult,
    Video,
    VideoStatus,
    Visibility,
)
from .playlist import PlaylistService
from .search import SearchService
from .upload import UploadController

__all__ = [
    "AnalyticsService",
    "AuthResolver",
    "load_cookie_file",
    "ChannelService",
    "YouTubeClient",
    "CommentService",
    "MetadataService",
    "PlaylistService",
    "SearchService",
    "UploadController",
    "Analytics",
    "AuthContext",
    "Channel",
    "Comment",
    "Playlist",
    "UploadResult",
    "Video",
    "VideoStatus",
    "Visibility",
    "AuthError",
    "AuthRequiredError",
    "NotFoundError",
    "RateLimitError",
    "UploadError",
    "UpstreamError",
    "YtmcpError",
]
