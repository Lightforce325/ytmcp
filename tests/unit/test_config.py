"""Tests for ytmcp.config: Settings defaults, overrides, env loading, caching."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytmcp.config import Settings, get_settings


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #
def test_settings_defaults(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path / "data")
    assert s.auth_mode == "auto"
    assert s.timeout == 30.0
    assert s.max_retries == 3
    assert s.api_port == 8765
    assert s.api_host == "127.0.0.1"
    assert s.mcp_transport == "stdio"
    assert s.log_level == "INFO"
    assert s.rate_limit_delay == 0.5
    assert s.cookie_file is None
    assert s.oauth_client_id is None
    assert s.oauth_client_secret is None
    assert s.oauth_refresh_token is None


def test_settings_user_agent_non_empty(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path / "data")
    assert s.user_agent
    assert "Mozilla" in s.user_agent


# --------------------------------------------------------------------------- #
# Constructor overrides
# --------------------------------------------------------------------------- #
def test_constructor_overrides(tmp_path: Path) -> None:
    cookie = tmp_path / "cookies.txt"
    data_dir = tmp_path / "mydata"
    s = Settings(
        data_dir=data_dir,
        cookie_file=cookie,
        rate_limit_delay=0.0,
        max_retries=1,
        timeout=5.5,
        auth_mode="cookie",
    )
    assert s.data_dir == data_dir
    assert s.cookie_file == cookie
    assert s.rate_limit_delay == 0.0
    assert s.max_retries == 1
    assert s.timeout == 5.5
    assert s.auth_mode == "cookie"


def test_cookie_file_parsed_as_path(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path / "data", cookie_file=str(tmp_path / "c.txt"))
    assert isinstance(s.cookie_file, Path)
    assert s.cookie_file == tmp_path / "c.txt"


def test_data_dir_parsed_as_path(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "data"
    s = Settings(data_dir=str(target))
    assert isinstance(s.data_dir, Path)
    assert s.data_dir == target


# --------------------------------------------------------------------------- #
# token_cache_path
# --------------------------------------------------------------------------- #
def test_token_cache_path(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path / "data")
    assert s.token_cache_path == (tmp_path / "data" / "oauth_token.json")


def test_token_cache_path_reflects_data_dir(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path / "other")
    assert s.token_cache_path.name == "oauth_token.json"
    assert s.token_cache_path.parent == tmp_path / "other"


# --------------------------------------------------------------------------- #
# get_settings caching
# --------------------------------------------------------------------------- #
def test_get_settings_returns_settings_instance() -> None:
    s = get_settings()
    assert isinstance(s, Settings)


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


# --------------------------------------------------------------------------- #
# Environment variable overrides (YTMCP_ prefix)
# --------------------------------------------------------------------------- #
def _fresh_settings() -> Settings:
    """Build a Settings with the lru_cache cleared (isolated from other tests)."""
    get_settings.cache_clear()
    return Settings()


def test_env_override_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YTMCP_TIMEOUT", "99.5")
    monkeypatch.setenv("YTMCP_DATA_DIR", str(tmp_path / "envdata"))
    s = _fresh_settings()
    assert s.timeout == 99.5


def test_env_override_max_retries_and_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("YTMCP_MAX_RETRIES", "7")
    monkeypatch.setenv("YTMCP_API_PORT", "9999")
    monkeypatch.setenv("YTMCP_DATA_DIR", str(tmp_path / "envdata"))
    s = _fresh_settings()
    assert s.max_retries == 7
    assert s.api_port == 9999


def test_env_override_auth_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YTMCP_AUTH_MODE", "oauth")
    monkeypatch.setenv("YTMCP_DATA_DIR", str(tmp_path / "envdata"))
    s = _fresh_settings()
    assert s.auth_mode == "oauth"


def test_get_settings_reads_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YTMCP_TIMEOUT", "12.0")
    monkeypatch.setenv("YTMCP_DATA_DIR", str(tmp_path / "envdata"))
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.timeout == 12.0
    finally:
        get_settings.cache_clear()


def test_env_var_takes_precedence_over_constructor_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Environment should populate fields when constructor does not pass them.
    monkeypatch.setenv("YTMCP_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("YTMCP_DATA_DIR", str(tmp_path / "envdata"))
    s = _fresh_settings()
    assert s.log_level == "DEBUG"


# --------------------------------------------------------------------------- #
# data_dir auto-creation
# --------------------------------------------------------------------------- #
def test_data_dir_is_created_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "fresh" / "deep" / "data"
    assert not target.exists()
    Settings(data_dir=target)
    assert target.exists()
    assert target.is_dir()


def test_data_dir_existing_is_kept(tmp_path: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("hi", encoding="utf-8")
    Settings(data_dir=target)
    assert target.exists()
    assert marker.exists()


def test_token_cache_path_dir_created(tmp_path: Path) -> None:
    target = tmp_path / "auto" / "created"
    s = Settings(data_dir=target)
    # The validator should have created the parent so the token cache is writable.
    assert s.token_cache_path.parent.exists()
