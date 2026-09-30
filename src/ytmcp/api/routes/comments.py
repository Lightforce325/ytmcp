"""REST routes for comments."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ...core import CommentService, YouTubeClient

router = APIRouter()


class ReplyBody(BaseModel):
    video_id: str
    text: str


class ModerateBody(BaseModel):
    action: str = "published"


@router.get("/{video_id}")
async def list_comments(video_id: str, limit: int = 20) -> list[dict[str, Any]]:
    async with YouTubeClient() as client:
        comments = await CommentService(client).list_comments(video_id, limit=limit)
        return [c.model_dump(mode="json") for c in comments]


@router.post("/{comment_id}/reply")
async def reply(comment_id: str, body: ReplyBody) -> dict[str, Any]:
    async with YouTubeClient() as client:
        comment = await CommentService(client).reply(body.video_id, comment_id, body.text)
        return comment.model_dump(mode="json")


@router.patch("/{comment_id}/moderate")
async def moderate(comment_id: str, body: ModerateBody) -> dict[str, Any]:
    async with YouTubeClient() as client:
        ok = await CommentService(client).moderate(comment_id, body.action)
        return {"ok": ok}
