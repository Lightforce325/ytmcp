"""Comment operations: list, reply, moderate."""

from __future__ import annotations

import logging
from typing import Any

from .client import YouTubeClient
from .exceptions import UpstreamError
from .models import Comment

logger = logging.getLogger(__name__)

NEXT_URL = "https://www.youtube.com/youtubei/v1/next"
COMMENT_CREATE = "https://www.youtube.com/youtubei/v1/comment/create_comment"
COMMENT_SET_STATE = "https://www.youtube.com/youtubei/v1/comment/set_comment_state"


class CommentService:
    """Read and write YouTube comments."""

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    async def list_comments(self, video_id: str, *, limit: int = 20) -> list[Comment]:
        """Fetch top-level comments for a video."""
        data = await self.client.innertube("next", {"videoId": video_id})
        section = (
            data.get("contents", {})
            .get("twoColumnWatchNextResults", {})
            .get("results", {})
            .get("results", {})
            .get("contents", [])
        )
        # Comments often load lazily; use the dedicated continuation when absent.
        found = _walk_comments(section)
        if not found:
            found = await self._comments_via_continuation(video_id, limit)
        return found[:limit]

    async def _comments_via_continuation(self, video_id: str, limit: int) -> list[Comment]:
        try:
            data = await self.client.innertube(
                "next", {"videoId": video_id, "params": "MgIQABoA"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Comment continuation failed: %s", exc)
            return []
        return _walk_comments(data)[:limit]

    async def reply(self, video_id: str, comment_id: str, text: str) -> Comment:
        """Post a reply to a comment."""
        body = {
            "context": self.client.innertube_payload()["context"],
            "commentText": text,
            "createReplyParams": _encode_create_reply_params(video_id, comment_id),
        }
        resp = await self.client.request("POST", COMMENT_CREATE, json=body, mutate=True)
        if resp.status_code >= 400:
            raise UpstreamError(f"Reply failed: {resp.status_code} {resp.text[:200]}")
        return Comment(comment_id="pending", video_id=video_id, text=text, is_reply=True)

    async def moderate(
        self, comment_id: str, action: str = "published"
    ) -> bool:
        """Set a comment's moderation state.

        Args:
            comment_id: Target comment.
            action: One of ``published``, ``heldForReview``, ``rejected``.
        """
        body = {
            "context": self.client.innertube_payload()["context"],
            "commentId": comment_id,
            "setStateParams": action,
        }
        resp = await self.client.request(
            "POST", COMMENT_SET_STATE, json=body, mutate=True
        )
        if resp.status_code >= 400:
            raise UpstreamError(f"Moderation failed: {resp.status_code}")
        return True


def _walk_comments(node: Any) -> list[Comment]:
    found: list[Comment] = []
    if isinstance(node, dict):
        # A ``commentThreadRenderer`` wraps a nested ``commentRenderer`` that
        # describes the *same* comment. Parse the wrapper, then recurse into the
        # *rest* of the wrapper (e.g. ``replies``) while skipping the nested
        # ``comment`` key, so the main comment is not emitted twice and its
        # replies are still discovered.
        for key in ("commentThreadRenderer", "commentRenderer"):
            if key in node and isinstance(node[key], dict):
                found.append(_parse_comment(node[key]))
        for k, v in node.items():
            if k == "commentThreadRenderer":
                if isinstance(v, dict):
                    remainder = {kk: vv for kk, vv in v.items() if kk != "comment"}
                    found.extend(_walk_comments(remainder))
                continue
            found.extend(_walk_comments(v))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_comments(item))
    return found


def _parse_comment(renderer: dict[str, Any]) -> Comment:
    entity = renderer.get("comment", {}).get("commentRenderer", renderer)
    cid = renderer.get("commentId") or entity.get("commentId", "")
    text = _runs_text(entity.get("contentText", {}).get("runs", []))
    author = _runs_text(entity.get("authorText", {}).get("runs", []))
    likes = entity.get("voteCount", {}).get("simpleText", "0")
    return Comment(
        comment_id=cid,
        author=author,
        text=text,
        like_count=int("".join(c for c in likes if c.isdigit()) or 0),
    )


def _runs_text(runs: Any) -> str:
    if isinstance(runs, str):
        return runs
    if isinstance(runs, list):
        return "".join(str(r.get("text", "")) for r in runs if isinstance(r, dict))
    return ""


def _encode_create_reply_params(video_id: str, comment_id: str) -> str:
    import base64

    raw = f"{video_id}|{comment_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")
