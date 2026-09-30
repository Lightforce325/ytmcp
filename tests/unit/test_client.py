"""Tests for the HTTP client: lifecycle, throttle, retry, cookies, and parsing."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core.client import (
    INNERTUBE_BASE,
    YouTubeClient,
    _parse_retry_after,
)
from ytmcp.core.exceptions import RateLimitError, UpstreamError

# Capture the genuine _backoff implementation before any monkeypatching.
_REAL_BACKOFF = YouTubeClient._backoff


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise exponential-backoff sleeps so retry tests run instantly.

    The default backoff is ``2**attempt`` seconds plus jitter; tests only assert
    the retry *behaviour*, not the delay. The ``_backoff`` tests below call the
    captured ``_REAL_BACKOFF`` directly with a stubbed ``asyncio.sleep``.
    """

    async def _instant(_self: YouTubeClient, _attempt: int) -> None:
        return None

    monkeypatch.setattr(YouTubeClient, "_backoff", _instant)


# --------------------------------------------------------------------------- #
# _parse_retry_after
# --------------------------------------------------------------------------- #
def test_parse_retry_after() -> None:
    assert _parse_retry_after("7") == 7.0
    assert _parse_retry_after(None) == 5.0
    assert _parse_retry_after("garbage") == 5.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 0.0),
        ("1.5", 1.5),
        ("  3 ", 3.0),
        ("-2", 0.0),  # negative is clamped to 0
        ("", 5.0),  # empty -> default
        ("abc", 5.0),  # non-numeric -> default
        (None, 5.0),
        ("1e2", 100.0),
    ],
)
def test_parse_retry_after_edges(value: str | None, expected: float) -> None:
    assert _parse_retry_after(value) == expected


# --------------------------------------------------------------------------- #
# Lifecycle: start / close / context manager
# --------------------------------------------------------------------------- #
async def test_aenter_starts_and_aexit_closes(settings: Settings) -> None:
    client = YouTubeClient(settings)
    assert client._client is None
    async with client as entered:
        assert entered is client
        assert client._client is not None
    # __aexit__ must have closed the underlying AsyncClient.
    assert client._client is None


async def test_start_is_idempotent(settings: Settings) -> None:
    client = YouTubeClient(settings)
    await client.start()
    first = client._client
    assert first is not None
    await client.start()
    # A second start() must not create a fresh client.
    assert client._client is first
    await client.close()


async def test_close_without_start_is_noop(settings: Settings) -> None:
    client = YouTubeClient(settings)
    # Should not raise even though the client was never started.
    await client.close()
    assert client._client is None


async def test_client_property_raises_before_start(settings: Settings) -> None:
    client = YouTubeClient(settings)
    with pytest.raises(RuntimeError, match="not started"):
        _ = client.client


async def test_client_property_returns_after_start(settings: Settings) -> None:
    client = YouTubeClient(settings)
    await client.start()
    assert isinstance(client.client, httpx.AsyncClient)
    await client.close()


async def test_close_is_idempotent(settings: Settings) -> None:
    client = YouTubeClient(settings)
    await client.start()
    await client.close()
    # Second close must not raise.
    await client.close()
    assert client._client is None


# --------------------------------------------------------------------------- #
# Cookies
# --------------------------------------------------------------------------- #
def test_set_cookies_updates_jar(settings: Settings) -> None:
    client = YouTubeClient(settings)
    assert client.cookies == {}
    client.set_cookies({"SID": "abc"})
    assert client.cookies == {"SID": "abc"}
    client.set_cookies({"SAPISID": "xyz"})
    assert client.cookies == {"SID": "abc", "SAPISID": "xyz"}


def test_cookies_property_returns_copy(settings: Settings) -> None:
    client = YouTubeClient(settings, cookies={"SID": "1"})
    snap = client.cookies
    snap["SID"] = "mutated"
    # Mutating the returned dict must not affect internal state.
    assert client.cookies == {"SID": "1"}


def test_constructor_copies_cookie_argument(settings: Settings) -> None:
    source = {"SID": "1"}
    client = YouTubeClient(settings, cookies=source)
    source["SID"] = "changed"
    assert client.cookies == {"SID": "1"}


def test_apply_cookies_injects_into_kwargs(settings: Settings) -> None:
    client = YouTubeClient(settings, cookies={"SID": "abc", "SAPISID": "xyz"})
    kwargs: dict = {}
    client._apply_cookies(kwargs)
    assert kwargs["cookies"] == {"SID": "abc", "SAPISID": "xyz"}


def test_apply_cookies_noop_when_empty(settings: Settings) -> None:
    client = YouTubeClient(settings)
    kwargs: dict = {}
    client._apply_cookies(kwargs)
    assert "cookies" not in kwargs


def test_apply_cookies_merges_into_existing_kwargs(settings: Settings) -> None:
    client = YouTubeClient(settings, cookies={"SID": "abc"})
    kwargs: dict = {"cookies": {"existing": "1"}}
    client._apply_cookies(kwargs)
    assert kwargs["cookies"] == {"existing": "1", "SID": "abc"}


@respx.mock
async def test_request_sends_cookies(settings: Settings) -> None:
    route = respx.get("https://example.com/cookie").mock(
        return_value=httpx.Response(200, json={})
    )
    client = YouTubeClient(settings, cookies={"SID": "abc"})
    async with client:
        await client.get("https://example.com/cookie")
    sent = route.calls.last.request
    assert "SID=abc" in sent.headers.get("cookie", "")


# --------------------------------------------------------------------------- #
# Rate limiting / throttle
# --------------------------------------------------------------------------- #
async def test_throttle_sleeps_when_called_twice(settings: Settings, monkeypatch) -> None:
    """_throttle enforces the configured delay between two immediate calls."""
    fast = settings.model_copy(update={"rate_limit_delay": 0.05})
    client = YouTubeClient(fast)

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr("ytmcp.core.client.asyncio.sleep", _fake_sleep)

    await client._throttle()  # first call: no prior request -> no sleep
    await client._throttle()  # second call: should sleep for ~delay
    assert any(d > 0 for d in sleeps)


@respx.mock
async def test_mutating_requests_are_throttled(settings: Settings, monkeypatch) -> None:
    """Two consecutive mutating requests both go through _throttle."""
    respx.post("https://example.com/m1").mock(return_value=httpx.Response(200))
    respx.post("https://example.com/m2").mock(return_value=httpx.Response(200))

    throttle_calls = []
    real_throttle = YouTubeClient._throttle

    async def _counting(self) -> None:
        throttle_calls.append(1)
        await real_throttle(self)

    monkeypatch.setattr(YouTubeClient, "_throttle", _counting)
    async with YouTubeClient(settings) as client:
        await client.post("https://example.com/m1")
        await client.post("https://example.com/m2")
    assert len(throttle_calls) == 2


@respx.mock
async def test_get_is_not_throttled(settings: Settings, monkeypatch) -> None:
    """GET requests do not pay the mutating throttle cost."""
    respx.get("https://example.com/g").mock(return_value=httpx.Response(200))
    throttle_calls = []
    real_throttle = YouTubeClient._throttle

    async def _counting(self) -> None:
        throttle_calls.append(1)
        await real_throttle(self)

    monkeypatch.setattr(YouTubeClient, "_throttle", _counting)
    async with YouTubeClient(settings) as client:
        await client.get("https://example.com/g")
    assert throttle_calls == []


# --------------------------------------------------------------------------- #
# request(): retries
# --------------------------------------------------------------------------- #
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


@respx.mock
async def test_rate_limit_retries_then_succeeds(settings: Settings) -> None:
    """429 on the first attempt is retried and can succeed (max_retries=1)."""
    responses = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"ok": True}),
    ]
    respx.get("https://example.com/lucky").mock(side_effect=responses)
    async with YouTubeClient(settings) as client:
        resp = await client.get("https://example.com/lucky")
    assert resp.status_code == 200


@respx.mock
async def test_rate_limit_records_retry_after(settings: Settings, monkeypatch) -> None:
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr("ytmcp.core.client.asyncio.sleep", _fake_sleep)
    respx.get("https://example.com/rl2").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "3"})
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(RateLimitError) as exc:
            await client.get("https://example.com/rl2")
    assert exc.value.retry_after == 3.0
    # The retry honoured the Retry-After value.
    assert 3.0 in sleeps


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


@respx.mock
async def test_5xx_exhausts_retries_raises_upstream_error(settings: Settings) -> None:
    respx.get("https://example.com/broken").mock(return_value=httpx.Response(503))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await client.get("https://example.com/broken")


@respx.mock
async def test_transport_error_retries_then_succeeds(settings: Settings) -> None:
    """A TransportError on the first attempt is retried and can succeed."""
    responses = [
        httpx.ConnectError("boom"),
        httpx.Response(200, json={"ok": True}),
    ]
    respx.get("https://example.com/net").mock(side_effect=responses)
    async with YouTubeClient(settings) as client:
        resp = await client.get("https://example.com/net")
    assert resp.status_code == 200


@respx.mock
async def test_timeout_exception_retries_then_succeeds(settings: Settings) -> None:
    """A TimeoutException on the first attempt is retried and can succeed."""
    responses = [
        httpx.ReadTimeout("slow"),
        httpx.Response(200, json={"ok": True}),
    ]
    respx.get("https://example.com/slow").mock(side_effect=responses)
    async with YouTubeClient(settings) as client:
        resp = await client.get("https://example.com/slow")
    assert resp.status_code == 200


@respx.mock
async def test_transport_error_exhausted_raises_upstream_error(settings: Settings) -> None:
    respx.get("https://example.com/dead").mock(side_effect=httpx.ConnectError("nope"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await client.get("https://example.com/dead")

@respx.mock
async def test_exhausted_rate_limit_like_transport_error_reraises_original(
    settings: Settings,
) -> None:
    """``request`` re-raises ``last_exc`` verbatim when it is a ``RateLimitError``.

    The normal 429 path raises ``RateLimitError`` immediately, so the trailing
    ``if isinstance(last_exc, RateLimitError): raise last_exc`` guard is only
    reachable when a *transport* failure that is also a ``RateLimitError``
    exhausts the retry budget. Such a hybrid exception is caught by the
    ``httpx.TransportError`` branch (setting ``last_exc``) yet satisfies the final
    ``isinstance`` check — pinning the guard's behaviour (re-raise the original,
    rather than wrapping it in ``UpstreamError``).
    """

    class HybridRateLimitError(httpx.ConnectError, RateLimitError):
        """A transport error that is *also* a ``RateLimitError``."""

    route = respx.get("https://example.com/ratelimited").mock(
        side_effect=HybridRateLimitError("too many requests")
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(RateLimitError):
            await client.get("https://example.com/ratelimited")

    # max_retries == 1 -> the request is attempted twice before giving up.
    assert route.call_count == settings.max_retries + 1


@respx.mock
async def test_4xx_returned_without_retry(settings: Settings) -> None:
    route = respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))
    async with YouTubeClient(settings) as client:
        resp = await client.get("https://example.com/missing")
    assert resp.status_code == 404
    assert route.call_count == 1


# --------------------------------------------------------------------------- #
# _backoff
# --------------------------------------------------------------------------- #
async def test_backoff_sleeps_with_exponential_base(settings: Settings, monkeypatch) -> None:
    """_backoff sleeps for 2**attempt (+ jitter), capped at 30s."""
    client = YouTubeClient(settings)
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr("ytmcp.core.client.asyncio.sleep", _fake_sleep)
    await _REAL_BACKOFF(client, 0)

    assert len(delays) == 1
    # 2**0 == 1, plus jitter in [0, 0.5).
    assert 1.0 <= delays[0] < 1.5


async def test_backoff_is_capped_at_30_seconds(settings: Settings, monkeypatch) -> None:
    """A large attempt index is capped at 30s (+ jitter)."""
    client = YouTubeClient(settings)
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr("ytmcp.core.client.asyncio.sleep", _fake_sleep)
    await _REAL_BACKOFF(client, 10)

    assert len(delays) == 1
    assert 30.0 <= delays[0] < 30.5


# --------------------------------------------------------------------------- #
# Convenience methods: put / delete
# --------------------------------------------------------------------------- #
@respx.mock
async def test_put_uses_mutate_and_method(settings: Settings) -> None:
    route = respx.put("https://example.com/put").mock(return_value=httpx.Response(200))
    async with YouTubeClient(settings) as client:
        resp = await client.put("https://example.com/put", json={"a": 1})
    assert resp.status_code == 200
    assert route.calls.last.request.method == "PUT"
    assert json.loads(route.calls.last.request.content.decode()) == {"a": 1}


@respx.mock
async def test_delete_uses_mutate_and_method(settings: Settings) -> None:
    route = respx.delete("https://example.com/del").mock(return_value=httpx.Response(204))
    async with YouTubeClient(settings) as client:
        resp = await client.delete("https://example.com/del")
    assert resp.status_code == 204
    assert route.calls.last.request.method == "DELETE"


@respx.mock
async def test_put_and_delete_are_throttled(settings: Settings, monkeypatch) -> None:
    respx.put("https://example.com/p").mock(return_value=httpx.Response(200))
    respx.delete("https://example.com/d").mock(return_value=httpx.Response(200))
    throttle_calls = []
    real_throttle = YouTubeClient._throttle

    async def _counting(self) -> None:
        throttle_calls.append(1)
        await real_throttle(self)

    monkeypatch.setattr(YouTubeClient, "_throttle", _counting)
    async with YouTubeClient(settings) as client:
        await client.put("https://example.com/p")
        await client.delete("https://example.com/d")
    assert len(throttle_calls) == 2


# --------------------------------------------------------------------------- #
# innertube_payload / innertube
# --------------------------------------------------------------------------- #
def test_innertube_payload_shape(settings: Settings) -> None:
    client = YouTubeClient(settings)
    payload = client.innertube_payload({"videoId": "x"})
    assert payload["context"]["client"]["clientName"] == "WEB"
    assert payload["videoId"] == "x"


def test_innertube_payload_without_extra(settings: Settings) -> None:
    client = YouTubeClient(settings)
    payload = client.innertube_payload()
    assert payload == {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": payload["context"]["client"]["clientVersion"],
                "hl": "en",
                "gl": "US",
            }
        }
    }


def test_innertube_payload_ignores_empty_extra(settings: Settings) -> None:
    client = YouTubeClient(settings)
    base = client.innertube_payload()
    same = client.innertube_payload({})
    assert base == same


@respx.mock
async def test_innertube_success_returns_json(settings: Settings) -> None:
    route = respx.post(url__regex=r".*youtubei/v1/next.*").mock(
        return_value=httpx.Response(200, json={"contents": {"x": 1}})
    )
    async with YouTubeClient(settings) as client:
        data = await client.innertube("next", {"videoId": "abc"})
    assert data == {"contents": {"x": 1}}

    request = route.calls.last.request
    # URL must carry the public web key + prettyPrint flag.
    assert "key=" in str(request.url)
    assert "prettyPrint=false" in str(request.url)
    body = json.loads(request.content.decode())
    assert body["videoId"] == "abc"
    assert body["context"]["client"]["clientName"] == "WEB"


async def test_innertube_url_construction(settings: Settings) -> None:
    """innertube() targets the InnerTube base URL for the endpoint."""
    assert INNERTUBE_BASE == "https://www.youtube.com/youtubei/v1"


@respx.mock
async def test_innertube_error_status_raises_upstream(settings: Settings) -> None:
    respx.post(url__regex=r".*youtubei/v1/browse.*").mock(return_value=httpx.Response(404))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError) as exc:
            await client.innertube("browse")
    assert exc.value.status_code == 404


@respx.mock
async def test_innertube_5xx_after_retries_raises_upstream(settings: Settings) -> None:
    respx.post(url__regex=r".*youtubei/v1/player.*").mock(return_value=httpx.Response(500))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UpstreamError):
            await client.innertube("player")


@respx.mock
async def test_innertube_mutate_flag(settings: Settings, monkeypatch) -> None:
    """innertube(mutate=True) routes through the mutating throttle."""
    respx.post(url__regex=r".*youtubei/v1/comment/create_comment.*").mock(
        return_value=httpx.Response(200, json={})
    )
    throttle_calls = []
    real_throttle = YouTubeClient._throttle

    async def _counting(self) -> None:
        throttle_calls.append(1)
        await real_throttle(self)

    monkeypatch.setattr(YouTubeClient, "_throttle", _counting)
    async with YouTubeClient(settings) as client:
        await client.innertube("comment/create_comment", {"x": 1}, mutate=True)
    assert throttle_calls == [1]


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_request_without_start_raises(settings: Settings) -> None:
    client = YouTubeClient(settings)
    with pytest.raises(RuntimeError):
        _ = client.client


def test_default_settings_used_when_none_passed() -> None:
    """Omitting settings falls back to get_settings()."""
    client = YouTubeClient()
    assert client.settings is not None
