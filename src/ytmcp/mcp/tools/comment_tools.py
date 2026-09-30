"""MCP tools for comments and moderation."""

from __future__ import annotations

import json
from typing import Any

from ...core import CommentService
from ._helpers import open_client


async def list_comments(video_id: str, limit: int = 20) -> str:
    """List top-level comments for a video.

    Args:
        video_id: The YouTube video id.
        limit: Maximum number of comments to return.
    """
    async with open_client() as client:
        comments = await CommentService(client).list_comments(video_id, limit=limit)
        return json.dumps([c.model_dump(mode="json") for c in comments], indent=2)


async def reply_to_comment(video_id: str, comment_id: str, text: str) -> str:
    """Reply to a comment on a video.

    Args:
        video_id: The video id the comment belongs to.
        comment_id: The comment to reply to.
        text: Reply text.
    """
    async with open_client(authenticated=True) as client:
        comment = await CommentService(client).reply(video_id, comment_id, text)
        return json.dumps(comment.model_dump(mode="json"), indent=2)


async def moderate_comment(comment_id: str, action: str = "published") -> str:
    """Moderate a comment (approve, hold, or reject).

    Args:
        comment_id: Target comment id.
        action: One of 'published', 'heldForReview', 'rejected'.
    """
    async with open_client(authenticated=True) as client:
        ok = await CommentService(client).moderate(comment_id, action)
        return f"Comment {comment_id} set to '{action}': {ok}"


def register(mcp: Any) -> None:
    """Register all comment tools with the MCP server."""
    for fn in (list_comments, reply_to_comment, moderate_comment):
        mcp.tool()(fn)


__all__ = ["list_comments", "reply_to_comment", "moderate_comment", "register"]
