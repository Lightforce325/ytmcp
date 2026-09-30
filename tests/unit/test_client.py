"""Tests for the HTTP client: throttle, retry, and parsing helpers."""

from __future__ import annotations

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core.client import YouTubeClient, _parse_retry_after
from ytmcp.core.exceptions import RateLimitError


def test_parse_retry_after() -> None:
    assert _parse_retry_after("7") == 7.0
    assert _parse_retry_after(None) == 5.0
    assert _parse_retry_after("garbage") == 5.0


@pytest.mark.asyncio
@respx.mock
async def test_get_success(settings: Settings) -> None:
    respx.get("https://example.com/ok").mock(return_value=httpx.Response(200, json={"a": 1}))
    async with YouTubeClient(settings) as client:
        resp = await client.get("https://example.com/ok")
        assert resp.status_code == 200
        assert resp.json() == {"a": 1}


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_raises(settings: Settings) -> None:
    route = respx.post("https://example.com/rl")
    route.mock(return_value=httpx.Response(429, headers={"Retry-After": "0"}))
    async with YouTubeClient(settings) as client:
        with pytest.raises(RateLimitError):
            await client.post("https://example.com/rl")


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_500_then_success(settings: Settings) -> None:
    responses = [
        httpx.Response(500),
        httpx.Response(200, json={"ok": True}),
    ]
    respx.get("https://example.com/flaky").mock(side_effect=responses)
    async with YouTubeClient(settings) as client:
        resp = await client.get("https://example.com/flaky")
        assert resp.status_code == 200


def test_innertube_payload_shape(settings: Settings) -> None:
    client = YouTubeClient(settings)
    payload = client.innertube_payload({"videoId": "x"})
    assert payload["context"]["client"]["clientName"] == "WEB"
    assert payload["videoId"] == "x"


@pytest.mark.asyncio
async def test_request_without_start_raises(settings: Settings) -> None:
    client = YouTubeClient(settings)
    with pytest.raises(RuntimeError):
        _ = client.client
