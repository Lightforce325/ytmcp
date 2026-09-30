"""Tests for cookie parsing and auth resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytmcp.config import Settings
from ytmcp.core.auth import AuthResolver, has_auth_cookies, load_cookie_file
from ytmcp.core.exceptions import AuthError, AuthRequiredError


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


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(AuthError):
        load_cookie_file(tmp_path / "nope.txt")


def test_resolve_cookie_mode(settings: Settings, netscape_cookies: Path) -> None:
    s = settings.model_copy(update={"cookie_file": netscape_cookies})
    ctx = AuthResolver(s).resolve()
    assert ctx.mode == "cookie"
    assert ctx.is_valid


def test_resolve_none_when_unconfigured(settings: Settings) -> None:
    ctx = AuthResolver(settings).resolve()
    assert ctx.mode == "none"
    assert not ctx.is_valid


@pytest.mark.asyncio
async def test_apply_without_auth_raises(settings: Settings) -> None:
    from ytmcp.core.client import YouTubeClient

    client = YouTubeClient(settings)
    with pytest.raises(AuthRequiredError):
        await AuthResolver(settings).apply(client)
