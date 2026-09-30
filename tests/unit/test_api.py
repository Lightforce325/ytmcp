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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """A TestClient bound to a freshly built app instance."""
    return TestClient(create_app())


# InnerTube endpoints hit by the read routes (POST .../youtubei/v1/<endpoint>).
NEXT_URL = "https://www.youtube.com/youtubei/v1/next"
SEARCH_URL = "https://www.youtube.com/youtubei/v1/search"


def _empty_next_payload() -> dict[str, object]:
    """A ``next`` response with no title/description -> triggers NotFoundError."""
    return {"contents": {"twoColumnWatchNextResults": {"results": {"results": {"contents": []}}}}}


def _search_payload() -> dict[str, object]:
    """A ``search`` response containing one videoRenderer."""
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
    assert len(resp.json()["paths"]) >= 19


@pytest.mark.parametrize(
    "prefix",
    ["/videos", "/playlists", "/comments", "/channel", "/analytics"],
)
def test_route_prefixes_registered(client: TestClient, prefix: str) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert any(p.startswith(prefix) for p in paths), f"no routes for {prefix}"


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
    payload = {
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
    respx.post(NEXT_URL).mock(return_value=httpx.Response(200, json=payload))
    resp = client.get("/videos/abc123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["video_id"] == "abc123"
    assert body["title"] == "My Video"
    assert body["view_count"] == 42


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
