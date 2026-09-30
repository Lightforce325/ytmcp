"""Tests for cookie parsing, OAuth flows, token cache, and auth resolution."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core.auth import (
    GOOGLE_DEVICE_CODE_URL,
    GOOGLE_TOKEN_URL,
    AuthResolver,
    OAuthProvider,
    _sleep,
    has_auth_cookies,
    load_cookie_file,
)
from ytmcp.core.client import YouTubeClient
from ytmcp.core.exceptions import AuthError, AuthRequiredError


# --------------------------------------------------------------------------- #
# Cookie parsing
# --------------------------------------------------------------------------- #
def test_load_netscape_cookies(netscape_cookies: Path) -> None:
    cookies = load_cookie_file(netscape_cookies)
    assert cookies["SAPISID"] == "abc123"
    assert cookies["SID"] == "sid456"

def test_load_json_cookies(json_cookies: Path) -> None:
    cookies = load_cookie_file(json_cookies)
    assert cookies["SAPISID"] == "xyz"
    assert cookies["LOGIN_INFO"] == "info"

def test_has_auth_cookies() -> None:
    assert has_auth_cookies({"SAPISID": "x"})
    assert has_auth_cookies({"SID": "x"})
    assert not has_auth_cookies({"foo": "bar"})

def test_has_auth_cookies_secure_3papisid() -> None:
    assert has_auth_cookies({"__Secure-3PAPISID": "x"})
    assert not has_auth_cookies({"__Secure-3PSID": "x"})

def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(AuthError):
        load_cookie_file(tmp_path / "nope.txt")

def test_load_json_plain_dict(tmp_path: Path) -> None:
    """A plain JSON dict (no ``cookies`` key) is a valid cookie jar."""
    path = tmp_path / "plain.json"
    path.write_text(json.dumps({"SAPISID": "abc", "SID": "sid"}), encoding="utf-8")
    cookies = load_cookie_file(path)
    assert cookies == {"SAPISID": "abc", "SID": "sid"}

def test_load_json_playwright_storage_state(tmp_path: Path) -> None:
    """Playwright ``storage_state`` wraps the list under ``{"cookies": [...]}``."""
    path = tmp_path / "storage.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "SAPISID", "value": "pw", "domain": ".youtube.com"},
                    {"name": "LOGIN_INFO", "value": "li"},
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    cookies = load_cookie_file(path)
    assert cookies == {"SAPISID": "pw", "LOGIN_INFO": "li"}

def test_load_json_invalid_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(AuthError):
        load_cookie_file(path)

def test_load_json_no_cookies_raises(tmp_path: Path) -> None:
    """A JSON list without name/value pairs yields no cookies -> AuthError."""
    path = tmp_path / "empty.json"
    path.write_text(json.dumps([{"foo": "bar"}]), encoding="utf-8")
    with pytest.raises(AuthError):
        load_cookie_file(path)

def test_load_json_skips_non_dict_and_partial_items(tmp_path: Path) -> None:
    """Non-dict entries and dicts missing name/value are skipped."""
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps(
            [
                "not-a-dict",
                42,
                {"name": "MISSING_VALUE"},
                {"value": "MISSING_NAME"},
                {"name": "SAPISID", "value": "kept"},
            ]
        ),
        encoding="utf-8",
    )
    cookies = load_cookie_file(path)
    assert cookies == {"SAPISID": "kept"}

def test_load_json_scalar_raises(tmp_path: Path) -> None:
    """A JSON scalar (neither list nor dict) has no cookies -> AuthError."""
    path = tmp_path / "scalar.json"
    path.write_text(json.dumps("just a string"), encoding="utf-8")
    with pytest.raises(AuthError):
        load_cookie_file(path)

def test_load_json_cookies_scalar_direct() -> None:
    """``_load_json_cookies`` on a scalar returns/raises for the non-list, non-dict path."""
    from ytmcp.core.auth import _load_json_cookies

    with pytest.raises(AuthError):
        _load_json_cookies(json.dumps(123))

def test_load_netscape_no_valid_cookies_raises(tmp_path: Path) -> None:
    path = tmp_path / "comments_only.txt"
    path.write_text("# just a comment\n\n", encoding="utf-8")
    with pytest.raises(AuthError):
        load_cookie_file(path)

def test_load_netscape_malformed_lines_ignored(tmp_path: Path) -> None:
    """Lines with < 7 tab-separated columns are skipped, valid ones kept."""
    path = tmp_path / "mixed.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        "too\tshort\tline\n"
        ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSAPISID\tgoodvalue\n"
        "another\tshort\n",
        encoding="utf-8",
    )
    cookies = load_cookie_file(path)
    assert cookies == {"SAPISID": "goodvalue"}

# --------------------------------------------------------------------------- #
# OAuthProvider: client requirements
# --------------------------------------------------------------------------- #
def _oauth_settings(settings: Settings, **update: object) -> Settings:
    """Return a settings copy configured for OAuth (client id + secret)."""
    base: dict[str, object] = {
        "oauth_client_id": "cid",
        "oauth_client_secret": "secret",
    }
    base.update(update)
    return settings.model_copy(update=base)

def test_require_client_missing_id(settings: Settings) -> None:
    provider = OAuthProvider(settings.model_copy(update={"oauth_client_secret": "secret"}))
    with pytest.raises(AuthRequiredError):
        provider._require_client()

def test_require_client_missing_secret(settings: Settings) -> None:
    provider = OAuthProvider(settings.model_copy(update={"oauth_client_id": "cid"}))
    with pytest.raises(AuthRequiredError):
        provider._require_client()

def test_require_client_ok(settings: Settings) -> None:
    provider = OAuthProvider(_oauth_settings(settings))
    assert provider._require_client() == ("cid", "secret")

# --------------------------------------------------------------------------- #
# OAuthProvider: device flow
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_start_device_flow_success(settings: Settings) -> None:
    route = respx.post(GOOGLE_DEVICE_CODE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "dev-code",
                "user_code": "USER-1",
                "verification_url": "https://google.com/device",
                "interval": 0,
            },
        )
    )
    provider = OAuthProvider(_oauth_settings(settings))
    async with YouTubeClient(settings) as client:
        data = await provider.start_device_flow(client)
    assert data["device_code"] == "dev-code"
    assert data["user_code"] == "USER-1"
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "client_id=cid" in body
    assert "device/code" not in body  # scope, not the url, is in the body

@pytest.mark.asyncio
@respx.mock
async def test_start_device_flow_failure(settings: Settings) -> None:
    respx.post(GOOGLE_DEVICE_CODE_URL).mock(
        return_value=httpx.Response(400, text="bad request")
    )
    provider = OAuthProvider(_oauth_settings(settings))
    async with YouTubeClient(settings) as client:
        with pytest.raises(AuthError):
            await provider.start_device_flow(client)

# --------------------------------------------------------------------------- #
# OAuthProvider: poll device token
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_poll_device_token_success_first_try(settings: Settings) -> None:
    route = respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        )
    )
    provider = OAuthProvider(_oauth_settings(settings))
    async with YouTubeClient(settings) as client:
        data = await provider.poll_device_token(client, "dev-code", interval=0)
    assert data["access_token"] == "at"
    assert route.call_count == 1
    # Token was persisted to the cache file.
    assert settings.token_cache_path.exists()
    assert provider._load_cached()["access_token"] == "at"

@pytest.mark.asyncio
@respx.mock
async def test_poll_device_token_pending_then_success(settings: Settings) -> None:
    responses = [
        httpx.Response(428, json={"error": "authorization_pending"}),
        httpx.Response(
            200, json={"access_token": "at2", "expires_in": 3600}
        ),
    ]
    route = respx.post(GOOGLE_TOKEN_URL).mock(side_effect=responses)
    provider = OAuthProvider(_oauth_settings(settings))
    async with YouTubeClient(settings) as client:
        data = await provider.poll_device_token(client, "dev-code", interval=0)
    assert data["access_token"] == "at2"
    assert route.call_count == 2

@pytest.mark.asyncio
@respx.mock
async def test_poll_device_token_slow_down_then_success(settings: Settings) -> None:
    responses = [
        httpx.Response(429, json={"error": "slow_down"}),
        httpx.Response(200, json={"access_token": "at3", "expires_in": 3600}),
    ]
    route = respx.post(GOOGLE_TOKEN_URL).mock(side_effect=responses)
    provider = OAuthProvider(_oauth_settings(settings))
    async with YouTubeClient(settings) as client:
        data = await provider.poll_device_token(client, "dev-code", interval=0)
    assert data["access_token"] == "at3"
    assert route.call_count == 2

@pytest.mark.asyncio
@respx.mock
async def test_poll_device_token_other_error_raises(settings: Settings) -> None:
    respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "access_denied"})
    )
    provider = OAuthProvider(_oauth_settings(settings))
    async with YouTubeClient(settings) as client:
        with pytest.raises(AuthError):
            await provider.poll_device_token(client, "dev-code", interval=0)

@pytest.mark.asyncio
@respx.mock
async def test_poll_device_token_timeout_raises(settings: Settings) -> None:
    # timeout=0 => the deadline has already passed, so the loop never runs.
    respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(428, json={"error": "authorization_pending"})
    )
    provider = OAuthProvider(_oauth_settings(settings))
    async with YouTubeClient(settings) as client:
        with pytest.raises(AuthError):
            await provider.poll_device_token(client, "dev-code", interval=0, timeout=0)

# --------------------------------------------------------------------------- #
# OAuthProvider: refresh
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_refresh_success(settings: Settings) -> None:
    route = respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
    )
    provider = OAuthProvider(_oauth_settings(settings, oauth_refresh_token="rt"))
    async with YouTubeClient(settings) as client:
        data = await provider.refresh(client)
    assert data["access_token"] == "new"
    # The refresh token is preserved when the response omits it.
    assert data["refresh_token"] == "rt"
    assert route.called
    assert provider._load_cached()["refresh_token"] == "rt"

@pytest.mark.asyncio
@respx.mock
async def test_refresh_failure(settings: Settings) -> None:
    respx.post(GOOGLE_TOKEN_URL).mock(return_value=httpx.Response(400, text="nope"))
    provider = OAuthProvider(_oauth_settings(settings, oauth_refresh_token="rt"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(AuthError):
            await provider.refresh(client)

@pytest.mark.asyncio
async def test_refresh_without_refresh_token_raises(settings: Settings) -> None:
    provider = OAuthProvider(_oauth_settings(settings))
    async with YouTubeClient(settings) as client:
        with pytest.raises(AuthRequiredError):
            await provider.refresh(client)

@pytest.mark.asyncio
async def test_refresh_keeps_existing_refresh_token_in_response(settings: Settings) -> None:
    provider = OAuthProvider(_oauth_settings(settings))
    # Pre-seed the cache with a refresh token so no settings token is needed.
    provider._save_token({"access_token": "old", "refresh_token": "cached-rt"})
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "fresh", "refresh_token": "server-rt"}
            )
        )
        async with YouTubeClient(settings) as client:
            data = await provider.refresh(client)
    assert data["refresh_token"] == "server-rt"
    assert provider._load_cached()["refresh_token"] == "server-rt"

# --------------------------------------------------------------------------- #
# OAuthProvider: token cache
# --------------------------------------------------------------------------- #
def test_save_token_writes_file(settings: Settings) -> None:
    provider = OAuthProvider(_oauth_settings(settings))
    provider._save_token({"access_token": "at", "expires_in": 3600})
    assert settings.token_cache_path.exists()
    stored = json.loads(settings.token_cache_path.read_text(encoding="utf-8"))
    assert stored["access_token"] == "at"
    assert "_saved_at" in stored

def test_load_cached_missing_file(settings: Settings) -> None:
    provider = OAuthProvider(_oauth_settings(settings))
    assert provider._load_cached() == {}

def test_load_cached_invalid_json(settings: Settings) -> None:
    settings.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
    settings.token_cache_path.write_text("{not json", encoding="utf-8")
    provider = OAuthProvider(_oauth_settings(settings))
    assert provider._load_cached() == {}

def test_cached_access_token_valid(settings: Settings) -> None:
    provider = OAuthProvider(_oauth_settings(settings))
    provider._save_token({"access_token": "valid", "expires_in": 3600})
    assert provider.cached_access_token() == "valid"

def test_cached_access_token_expired(settings: Settings) -> None:
    provider = OAuthProvider(_oauth_settings(settings))
    provider._save_token({"access_token": "stale", "expires_in": 3600})
    # Rewrite the cache with an old save time to simulate expiry.
    data = json.loads(settings.token_cache_path.read_text(encoding="utf-8"))
    data["_saved_at"] = time.time() - 10_000
    settings.token_cache_path.write_text(json.dumps(data), encoding="utf-8")
    assert provider.cached_access_token() is None

def test_cached_access_token_no_token(settings: Settings) -> None:
    provider = OAuthProvider(_oauth_settings(settings))
    provider._save_token({"expires_in": 3600})
    assert provider.cached_access_token() is None

# --------------------------------------------------------------------------- #
# _sleep helper
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sleep_is_awaitable() -> None:
    await _sleep(0.0)

# --------------------------------------------------------------------------- #
# AuthResolver.resolve
# --------------------------------------------------------------------------- #
def test_resolve_cookie_mode(settings: Settings, netscape_cookies: Path) -> None:
    s = settings.model_copy(update={"cookie_file": netscape_cookies})
    ctx = AuthResolver(s).resolve()
    assert ctx.mode == "cookie"
    assert ctx.is_valid

def test_resolve_none_when_unconfigured(settings: Settings) -> None:
    ctx = AuthResolver(settings).resolve()
    assert ctx.mode == "none"
    assert not ctx.is_valid

def test_resolve_oauth_mode_explicit(settings: Settings) -> None:
    s = settings.model_copy(update={"auth_mode": "oauth"})
    ctx = AuthResolver(s).resolve()
    assert ctx.mode == "oauth"
    assert ctx.is_valid

def test_resolve_cookie_mode_explicit(settings: Settings, netscape_cookies: Path) -> None:
    s = settings.model_copy(update={"auth_mode": "cookie", "cookie_file": netscape_cookies})
    ctx = AuthResolver(s).resolve()
    assert ctx.mode == "cookie"
    assert ctx.is_valid

def test_resolve_auto_with_refresh_token(settings: Settings) -> None:
    s = settings.model_copy(update={"auth_mode": "auto", "oauth_refresh_token": "rt"})
    ctx = AuthResolver(s).resolve()
    assert ctx.mode == "oauth"

def test_resolve_auto_with_cache_file(settings: Settings) -> None:
    settings.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
    settings.token_cache_path.write_text("{}", encoding="utf-8")
    ctx = AuthResolver(settings.model_copy(update={"auth_mode": "auto"})).resolve()
    assert ctx.mode == "oauth"

def test_resolve_auto_with_cookie_file(settings: Settings, netscape_cookies: Path) -> None:
    s = settings.model_copy(update={"auth_mode": "auto", "cookie_file": netscape_cookies})
    ctx = AuthResolver(s).resolve()
    assert ctx.mode == "cookie"

def test_resolve_auto_nothing_configured(settings: Settings) -> None:
    ctx = AuthResolver(settings.model_copy(update={"auth_mode": "auto"})).resolve()
    assert ctx.mode == "none"

# --------------------------------------------------------------------------- #
# AuthResolver.apply
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_apply_without_auth_raises(settings: Settings) -> None:
    client = YouTubeClient(settings)
    with pytest.raises(AuthRequiredError):
        await AuthResolver(settings).apply(client)

@pytest.mark.asyncio
async def test_apply_cookie_mode_attaches_cookies(
    settings: Settings, netscape_cookies: Path
) -> None:
    s = settings.model_copy(update={"cookie_file": netscape_cookies})
    client = YouTubeClient(s)
    ctx = await AuthResolver(s).apply(client)
    assert ctx.mode == "cookie"
    assert ctx.is_valid
    assert client.cookies["SAPISID"] == "abc123"

@pytest.mark.asyncio
async def test_apply_cookie_mode_warns_without_auth_cookies(
    settings: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    jar = tmp_path / "non_auth.json"
    jar.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    s = settings.model_copy(update={"cookie_file": jar})
    client = YouTubeClient(s)
    with caplog.at_level("WARNING", logger="ytmcp.core.auth"):
        ctx = await AuthResolver(s).apply(client)
    assert ctx.is_valid
    assert client.cookies["foo"] == "bar"
    assert any("no YouTube auth cookies" in r.message for r in caplog.records)

@pytest.mark.asyncio
@respx.mock
async def test_apply_oauth_with_cached_token(settings: Settings) -> None:
    s = _oauth_settings(settings, auth_mode="oauth")
    provider = OAuthProvider(s)
    provider._save_token({"access_token": "cached-at", "expires_in": 3600})
    client = YouTubeClient(s)
    ctx = await AuthResolver(s).apply(client)
    assert ctx.mode == "oauth"
    assert ctx.is_valid
    assert client.client.headers["Authorization"] == "Bearer cached-at"

@pytest.mark.asyncio
@respx.mock
async def test_apply_oauth_refreshes_when_needed(settings: Settings) -> None:
    s = _oauth_settings(settings, auth_mode="oauth", oauth_refresh_token="rt")
    provider = OAuthProvider(s)
    # Expired cache entry forces a refresh.
    provider._save_token({"access_token": "stale", "expires_in": 3600})
    data = json.loads(s.token_cache_path.read_text(encoding="utf-8"))
    data["_saved_at"] = time.time() - 10_000
    s.token_cache_path.write_text(json.dumps(data), encoding="utf-8")

    route = respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "refreshed", "expires_in": 3600})
    )
    async with YouTubeClient(s) as client:
        ctx = await AuthResolver(s).apply(client)
        auth_header = client.client.headers["Authorization"]
    assert route.called
    assert ctx.is_valid
    assert ctx.expires_at is not None
    assert auth_header == "Bearer refreshed"

@pytest.mark.asyncio
@respx.mock
async def test_apply_oauth_token_none_raises(settings: Settings) -> None:
    """Refresh succeeds but returns no access token -> AuthError."""
    s = _oauth_settings(settings, auth_mode="oauth", oauth_refresh_token="rt")
    respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"expires_in": 3600})
    )
    async with YouTubeClient(s) as client:
        with pytest.raises(AuthError):
            await AuthResolver(s).apply(client)

@pytest.mark.asyncio
@respx.mock
async def test_apply_expires_at_is_in_the_future(settings: Settings) -> None:
    s = _oauth_settings(settings, auth_mode="oauth", oauth_refresh_token="rt")
    respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 7200})
    )
    async with YouTubeClient(s) as client:
        ctx = await AuthResolver(s).apply(client)
    assert ctx.expires_at is not None
    assert ctx.expires_at > datetime.now(UTC)
