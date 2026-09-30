"""Tests for the FastAPI REST surface (app + all routes).

All upstream YouTube traffic is intercepted with ``respx``; no real network
calls are made. The app is exercised through ``fastapi.testclient.TestClient``.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from ytmcp.api.app import app, create_app
from ytmcp.core.exceptions import NotFoundError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to a freshly built app instance."""
    return TestClient(create_app())


# InnerTube endpoints hit by the read routes (POST .../youtubei/v1/<endpoint>).
NEXT_URL = "https://www.youtube.com/youtubei/v1/next"
SEARCH_URL = "https://www.youtube.com/youtubei/v1/search"

# Routes that must be registered on the app (trailing prefixes / exact paths).
EXPECTED_ROUTE_PREFIXES = {
    "/videos": ["/videos"],
    "/playlists": ["/playlists"],
    "/comments": ["/comments"],
    "/channel": ["/channel"],
    "/analytics": ["/analytics"],
}
MIN_PATHS = 19


def _empty_next_payload() -> dict[str, object]:
    """Return a ``next`` response with no title/description (-> NotFoundError)."""
    return {"contents": {"twoColumnWatchNextResults": {"results": {"results": {"contents": []}}}}}


def _search_payload() -> dict[str, object]:
    """Return a ``search`` response containing a single videoRenderer."""
    return {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "sectionListRenderer": {
                        "contents": [
                            {
                                "itemSectionRenderer": {
                                    "contents": [
                                        {
                                            "videoRenderer": {
                                                "videoId": "abc123",
                                                "title": {"runs": [{"text": "Hello World"}]},
                                                "viewCountText": {"simpleText": "1,234 views"},
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        }
    }


def _video_next_payload() -> dict[str, object]:
    """Return a ``next`` response with full primary + secondary info renderers."""
    return {
        "contents": {
            "twoColumnWatchNextResults": {
                "results": {
                    "results": {
                        "contents": [
                            {
                                "videoPrimaryInfoRenderer": {
                                    "title": {"runs": [{"text": "My Video"}]},
                                    "viewCount": {
                                        "videoViewCountRenderer": {
                                            "viewCount": {"runs": [{"text": "42 views"}]}
                                        }
                                    },
                                }
                            },
                            {
                                "videoSecondaryInfoRenderer": {
                                    "attributedDescription": {"content": "A description"}
                                }
                            },
                        ]
                    }
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# App wiring / metadata
# ---------------------------------------------------------------------------


def test_app_exposes_module_level_instance() -> None:
    assert app.title == "ytmcp REST API"


def test_health_returns_ok_and_auth_mode(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "auth_mode" in body


def test_openapi_schema_has_at_least_19_paths(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert len(resp.json()["paths"]) >= MIN_PATHS


@pytest.mark.parametrize("prefix", sorted(EXPECTED_ROUTE_PREFIXES))
def test_route_prefixes_registered(client: TestClient, prefix: str) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert any(p.startswith(prefix) for p in paths), f"no routes registered for {prefix}"


@pytest.mark.parametrize("prefix", sorted(EXPECTED_ROUTE_PREFIXES))
def test_each_prefix_has_at_least_one_expected_path(client: TestClient, prefix: str) -> None:
    """Each documented prefix must own its own registered route paths."""
    paths = client.get("/openapi.json").json()["paths"]
    owned = [p for p in paths if p.startswith(f"{prefix}/") or p == prefix]
    assert owned, f"{prefix} owns no paths (only appears as a substring)"


# ---------------------------------------------------------------------------
# Read endpoints (respx-mocked upstream)
# ---------------------------------------------------------------------------


@respx.mock
def test_search_returns_valid_json(client: TestClient) -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=_search_payload()))
    resp = client.get("/videos/search", params={"q": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["video_id"] == "abc123"
    assert data[0]["title"] == "Hello World"


@respx.mock
def test_get_video_returns_valid_json(client: TestClient) -> None:
    respx.post(NEXT_URL).mock(return_value=httpx.Response(200, json=_video_next_payload()))
    resp = client.get("/videos/abc123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["video_id"] == "abc123"
    assert body["title"] == "My Video"
    assert body["view_count"] == 42
    assert body["description"] == "A description"


# ---------------------------------------------------------------------------
# Not-found handling
# ---------------------------------------------------------------------------


@respx.mock
def test_get_video_missing_returns_404(client: TestClient) -> None:
    # No title/description in the payload -> MetadataService raises NotFoundError.
    # The route exists (so this 404 is not a routing miss), and the mocked
    # upstream call must actually have been made.
    assert "/videos/{video_id}" in client.get("/openapi.json").json()["paths"]
    route = respx.post(NEXT_URL).mock(
        return_value=httpx.Response(200, json=_empty_next_payload())
    )
    resp = client.get("/videos/does-not-exist")
    assert resp.status_code == 404
    assert route.called


@respx.mock
def test_get_video_404_when_service_raises_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directly mock ``MetadataService.get_video`` raising ``NotFoundError``."""

    async def _raise(self: object, video_id: str) -> object:  # noqa: ANN001
        raise NotFoundError(f"Video not found: {video_id}")

    monkeypatch.setattr("ytmcp.core.metadata.MetadataService.get_video", _raise)
    resp = client.get("/videos/ghost")
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Request-body validation
# ---------------------------------------------------------------------------


@respx.mock
def test_patch_metadata_rejects_invalid_body(client: TestClient) -> None:
    # ``tags`` must be a list[str]; a bare string fails validation -> 422.
    resp = client.patch("/videos/abc123/metadata", json={"tags": "bukan-list"})
    assert resp.status_code == 422


@respx.mock
def test_patch_metadata_accepts_valid_body(client: TestClient) -> None:
    respx.post(url__regex=r"https://studio\.youtube\.com/.*").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    resp = client.patch("/videos/abc123/metadata", json={"title": "Baru", "tags": ["a", "b"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["video_id"] == "abc123"
    assert "title" in body["updated_fields"]

# ---------------------------------------------------------------------------
# app.main() entry point
# ---------------------------------------------------------------------------

def test_main_invokes_uvicorn_with_settings_host_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main()`` must call ``uvicorn.run`` with host/port from settings."""
    import uvicorn

    from ytmcp.api import app as app_mod
    from ytmcp.config import Settings

    captured: dict[str, object] = {}

    def _fake_run(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    fake_settings = Settings(api_host="0.0.0.0", api_port=9123)
    monkeypatch.setattr(uvicorn, "run", _fake_run)
    monkeypatch.setattr(app_mod, "get_settings", lambda: fake_settings)

    app_mod.main()

    assert captured["kwargs"]["host"] == "0.0.0.0"
    assert captured["kwargs"]["port"] == 9123
    assert captured["args"][0] is app_mod.app

def test_main_uses_module_level_app_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from ytmcp.api import app as app_mod
    from ytmcp.config import Settings

    seen: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: seen.append(a[0]))
    monkeypatch.setattr(app_mod, "get_settings", lambda: Settings())

    app_mod.main()

    assert seen == [app_mod.app]

# ---------------------------------------------------------------------------
# /playlists routes
# ---------------------------------------------------------------------------

def test_create_playlist_returns_200_and_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import PlaylistService
    from ytmcp.core.models import Playlist, Visibility

    captured: dict[str, object] = {}

    async def _fake_create(
        self: object,
        title: str,
        *,
        description: str = "",
        visibility: Visibility = Visibility.PRIVATE,
    ) -> Playlist:
        captured.update(title=title, description=description, visibility=visibility)
        return Playlist(
            playlist_id="PL123",
            title=title,
            description=description,
            visibility=visibility,
        )

    monkeypatch.setattr(PlaylistService, "create", _fake_create)

    resp = client.post(
        "/playlists",
        json={"title": "My List", "description": "desc", "visibility": "public"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["playlist_id"] == "PL123"
    assert body["title"] == "My List"
    assert body["visibility"] == "PUBLIC"
    assert captured["visibility"] == Visibility.PUBLIC
    assert captured["description"] == "desc"

def test_create_playlist_rejects_missing_title(client: TestClient) -> None:
    resp = client.post("/playlists", json={"description": "no title"})
    assert resp.status_code == 422

def test_list_playlists_returns_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import PlaylistService
    from ytmcp.core.models import Playlist

    async def _fake_list(self: object, channel_id: str) -> list[Playlist]:
        assert channel_id == "UC123"
        return [Playlist(playlist_id="PL1", title="A"), Playlist(playlist_id="PL2", title="B")]

    monkeypatch.setattr(PlaylistService, "list_playlists", _fake_list)

    resp = client.get("/playlists/channel/UC123")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert [p["playlist_id"] for p in data] == ["PL1", "PL2"]

def test_add_video_to_playlist_returns_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import PlaylistService

    async def _fake_add(self: object, playlist_id: str, video_id: str) -> bool:
        assert (playlist_id, video_id) == ("PL1", "vid9")
        return True

    monkeypatch.setattr(PlaylistService, "add_video", _fake_add)

    resp = client.post("/playlists/PL1/videos", json={"video_id": "vid9"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

def test_remove_video_from_playlist_returns_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import PlaylistService

    async def _fake_remove(self: object, playlist_id: str, video_id: str) -> bool:
        assert (playlist_id, video_id) == ("PL1", "vid9")
        return True

    monkeypatch.setattr(PlaylistService, "remove_video", _fake_remove)

    resp = client.delete("/playlists/PL1/videos/vid9")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

def test_delete_playlist_returns_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import PlaylistService

    async def _fake_delete(self: object, playlist_id: str) -> bool:
        assert playlist_id == "PL1"
        return True

    monkeypatch.setattr(PlaylistService, "delete", _fake_delete)

    resp = client.delete("/playlists/PL1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

@respx.mock
def test_create_playlist_end_to_end_via_respx(client: TestClient) -> None:
    respx.post(url__regex=r"https://studio\.youtube\.com/.*").mock(
        return_value=httpx.Response(200, json={"playlistId": "PLREAL"})
    )
    resp = client.post("/playlists", json={"title": "Real", "visibility": "private"})
    assert resp.status_code == 200
    assert resp.json()["playlist_id"] == "PLREAL"

# ---------------------------------------------------------------------------
# /comments routes
# ---------------------------------------------------------------------------

def test_list_comments_returns_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import CommentService
    from ytmcp.core.models import Comment

    captured: dict[str, object] = {}

    async def _fake_list(self: object, video_id: str, *, limit: int = 20) -> list[Comment]:
        captured.update(video_id=video_id, limit=limit)
        return [Comment(comment_id="c1", video_id=video_id, text="hi", author="Bob")]

    monkeypatch.setattr(CommentService, "list_comments", _fake_list)

    resp = client.get("/comments/vid1", params={"limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["comment_id"] == "c1"
    assert data[0]["author"] == "Bob"
    assert captured == {"video_id": "vid1", "limit": 5}

def test_reply_to_comment_returns_comment(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import CommentService
    from ytmcp.core.models import Comment

    captured: dict[str, object] = {}

    async def _fake_reply(
        self: object, video_id: str, comment_id: str, text: str
    ) -> Comment:
        captured.update(video_id=video_id, comment_id=comment_id, text=text)
        return Comment(
            comment_id="new1", video_id=video_id, text=text, is_reply=True, parent_id=comment_id
        )

    monkeypatch.setattr(CommentService, "reply", _fake_reply)

    resp = client.post(
        "/comments/parent1/reply", json={"video_id": "vid1", "text": "nice"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["comment_id"] == "new1"
    assert body["text"] == "nice"
    assert body["is_reply"] is True
    assert captured == {"video_id": "vid1", "comment_id": "parent1", "text": "nice"}

def test_reply_rejects_missing_fields(client: TestClient) -> None:
    resp = client.post("/comments/parent1/reply", json={"text": "no video id"})
    assert resp.status_code == 422

@pytest.mark.parametrize("action", ["published", "held", "rejected"])
def test_moderate_comment_returns_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    from ytmcp.core import CommentService

    captured: dict[str, object] = {}

    async def _fake_moderate(self: object, comment_id: str, act: str) -> bool:
        captured.update(comment_id=comment_id, action=act)
        return True

    monkeypatch.setattr(CommentService, "moderate", _fake_moderate)

    resp = client.patch("/comments/c1/moderate", json={"action": action})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert captured == {"comment_id": "c1", "action": action}

def test_moderate_default_action_is_published(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import CommentService

    captured: list[str] = []

    async def _fake_moderate(self: object, comment_id: str, act: str) -> bool:
        captured.append(act)
        return False

    monkeypatch.setattr(CommentService, "moderate", _fake_moderate)

    resp = client.patch("/comments/c1/moderate", json={})
    assert resp.status_code == 200
    assert resp.json() == {"ok": False}
    assert captured == ["published"]

# ---------------------------------------------------------------------------
# /channel routes
# ---------------------------------------------------------------------------

def test_get_channel_returns_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import ChannelService
    from ytmcp.core.models import Channel

    async def _fake_info(self: object, channel_id: str | None = None) -> Channel:
        assert channel_id == "UC1"
        return Channel(channel_id="UC1", title="Chan", subscriber_count=42)

    monkeypatch.setattr(ChannelService, "get_info", _fake_info)

    resp = client.get("/channel/UC1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel_id"] == "UC1"
    assert body["title"] == "Chan"
    assert body["subscriber_count"] == 42

def test_get_subscribers_returns_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import ChannelService

    async def _fake_count(self: object, channel_id: str | None = None) -> int | None:
        assert channel_id == "UC1"
        return 1234

    monkeypatch.setattr(ChannelService, "get_subscriber_count", _fake_count)

    resp = client.get("/channel/UC1/subscribers")
    assert resp.status_code == 200
    assert resp.json() == {"channel_id": "UC1", "subscribers": 1234}

def test_update_branding_returns_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    from ytmcp.core import ChannelService

    captured: dict[str, Any] = {}

    async def _fake_update(
        self: object,
        *,
        banner_path: str | None = None,
        avatar_path: str | None = None,
        links: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        captured.update(banner_path=banner_path, avatar_path=avatar_path, links=links)
        return {"banner": {"status": "staged"}}

    monkeypatch.setattr(ChannelService, "update_branding", _fake_update)

    resp = client.patch("/channel/UC1/branding", json={"banner_path": "/tmp/b.png"})
    assert resp.status_code == 200
    assert resp.json() == {"banner": {"status": "staged"}}
    assert captured["banner_path"] == "/tmp/b.png"
    assert captured["avatar_path"] is None

def test_update_branding_accepts_empty_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import ChannelService

    async def _fake_update(self: object, **kwargs: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(ChannelService, "update_branding", _fake_update)

    resp = client.patch("/channel/UC1/branding", json={})
    assert resp.status_code == 200
    assert resp.json() == {}

# ---------------------------------------------------------------------------
# /analytics routes
# ---------------------------------------------------------------------------

def test_channel_analytics_returns_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import AnalyticsService
    from ytmcp.core.models import Analytics

    captured: dict[str, object] = {}

    async def _fake_analytics(
        self: object, channel_id: str, *, period_days: int = 28
    ) -> Analytics:
        captured.update(channel_id=channel_id, period_days=period_days)
        return Analytics(
            channel_id=channel_id, period_days=period_days, views=500, watch_time_minutes=1.5
        )

    monkeypatch.setattr(AnalyticsService, "get_channel_analytics", _fake_analytics)

    resp = client.get("/analytics/UC1", params={"period_days": 7})
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel_id"] == "UC1"
    assert body["period_days"] == 7
    assert body["views"] == 500
    assert body["watch_time_minutes"] == 1.5
    assert captured == {"channel_id": "UC1", "period_days": 7}

def test_channel_analytics_default_period(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import AnalyticsService
    from ytmcp.core.models import Analytics

    captured: dict[str, object] = {}

    async def _fake_analytics(
        self: object, channel_id: str, *, period_days: int = 28
    ) -> Analytics:
        captured["period_days"] = period_days
        return Analytics(channel_id=channel_id, period_days=period_days)

    monkeypatch.setattr(AnalyticsService, "get_channel_analytics", _fake_analytics)

    resp = client.get("/analytics/UC1")
    assert resp.status_code == 200
    assert captured["period_days"] == 28

def test_top_videos_returns_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import AnalyticsService
    from ytmcp.core.models import Video

    captured: dict[str, object] = {}

    async def _fake_top(
        self: object, channel_id: str, *, limit: int = 10
    ) -> list[Video]:
        captured.update(channel_id=channel_id, limit=limit)
        return [Video(video_id="v1", title="Top", view_count=99)]

    monkeypatch.setattr(AnalyticsService, "get_top_videos", _fake_top)

    resp = client.get("/analytics/UC1/top-videos", params={"limit": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["video_id"] == "v1"
    assert data[0]["view_count"] == 99
    assert captured == {"channel_id": "UC1", "limit": 3}

# ---------------------------------------------------------------------------
# /videos routes (channel listing, visibility, delete)
# ---------------------------------------------------------------------------

def test_list_channel_videos_returns_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import SearchService
    from ytmcp.core.models import Video

    captured: dict[str, object] = {}

    async def _fake_list(
        self: object, channel_id: str, *, limit: int = 30
    ) -> list[Video]:
        captured.update(channel_id=channel_id, limit=limit)
        return [Video(video_id="a1", title="First")]

    monkeypatch.setattr(SearchService, "list_channel_videos", _fake_list)

    resp = client.get("/videos/channel/UC1", params={"limit": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["video_id"] == "a1"
    assert captured == {"channel_id": "UC1", "limit": 10}

def test_set_visibility_returns_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import MetadataService
    from ytmcp.core.models import Visibility

    captured: dict[str, object] = {}

    async def _fake_vis(
        self: object,
        video_id: str,
        visibility: Visibility,
        *,
        publish_at: str | None = None,
    ) -> dict[str, object]:
        captured.update(video_id=video_id, visibility=visibility, publish_at=publish_at)
        return {"video_id": video_id, "visibility": visibility.value}

    monkeypatch.setattr(MetadataService, "set_visibility", _fake_vis)

    resp = client.patch(
        "/videos/vid1/visibility",
        json={"visibility": "unlisted", "publish_at": "2026-01-01T00:00:00Z"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["video_id"] == "vid1"
    assert body["visibility"] == "UNLISTED"
    assert captured["visibility"] == Visibility.UNLISTED
    assert captured["publish_at"] == "2026-01-01T00:00:00Z"

def test_set_visibility_rejects_missing_visibility(client: TestClient) -> None:
    resp = client.patch("/videos/vid1/visibility", json={})
    assert resp.status_code == 422

def test_delete_video_returns_deleted_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ytmcp.core import MetadataService

    called: list[str] = []

    async def _fake_delete(self: object, video_id: str) -> bool:
        called.append(video_id)
        return True

    monkeypatch.setattr(MetadataService, "delete_video", _fake_delete)

    resp = client.delete("/videos/vid1")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "vid1"}
    assert called == ["vid1"]
