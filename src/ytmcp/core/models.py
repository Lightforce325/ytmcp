"""Pydantic data models shared across ytmcp."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Visibility(StrEnum):
    """Video visibility states."""

    PUBLIC = "PUBLIC"
    UNLISTED = "UNLISTED"
    PRIVATE = "PRIVATE"
    SCHEDULED = "SCHEDULED"


class VideoStatus(BaseModel):
    """Lifecycle status of an uploaded video."""

    upload_status: str | None = Field(default=None, description="e.g. uploaded, processing")
    privacy_status: Visibility | None = None
    rejection_reason: str | None = None
    publish_at: datetime | None = None


class Thumbnail(BaseModel):
    """A single thumbnail size variant."""

    url: str
    width: int | None = None
    height: int | None = None


class Video(BaseModel):
    """A YouTube video resource (trimmed to useful fields)."""

    video_id: str
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    category_id: str | None = None
    default_language: str | None = None
    duration_seconds: int | None = None
    published_at: datetime | None = None
    visibility: Visibility | None = None
    thumbnails: dict[str, Thumbnail] = Field(default_factory=dict)
    channel_id: str | None = None
    channel_title: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    url: str | None = None

    @property
    def watch_url(self) -> str:
        return self.url or f"https://www.youtube.com/watch?v={self.video_id}"


class Channel(BaseModel):
    """A YouTube channel resource."""

    channel_id: str
    title: str = ""
    description: str = ""
    subscriber_count: int | None = None
    video_count: int | None = None
    view_count: int | None = None
    country: str | None = None
    custom_url: str | None = None
    avatar_url: str | None = None
    banner_url: str | None = None


class Playlist(BaseModel):
    """A YouTube playlist resource."""

    playlist_id: str
    title: str = ""
    description: str = ""
    visibility: Visibility | None = None
    item_count: int | None = None
    video_ids: list[str] = Field(default_factory=list)


class Comment(BaseModel):
    """A YouTube comment resource."""

    comment_id: str
    video_id: str | None = None
    author: str = ""
    author_channel_id: str | None = None
    text: str = ""
    like_count: int | None = None
    published_at: datetime | None = None
    is_reply: bool = False
    parent_id: str | None = None


class Analytics(BaseModel):
    """Aggregated channel analytics snapshot."""

    channel_id: str
    period_days: int = 28
    views: int | None = None
    watch_time_minutes: float | None = None
    subscribers_gained: int | None = None
    subscribers_lost: int | None = None
    estimated_revenue: float | None = None
    top_videos: list[Video] = Field(default_factory=list)


class UploadResult(BaseModel):
    """Result of a video upload operation."""

    video_id: str
    title: str
    status: VideoStatus
    url: str


class AuthContext(BaseModel):
    """Represents the resolved authentication context."""

    mode: str  # "oauth" | "cookie"
    is_valid: bool = False
    expires_at: datetime | None = None
    account_name: str | None = None
