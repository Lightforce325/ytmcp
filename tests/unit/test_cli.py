"""Tests for the Typer CLI (ytmcp.cli).

These tests exercise only the *safe* commands that never touch the network
(``version``, ``config``, ``auth status``, ``auth verify-cookies`` and
``--help``). Commands that perform real HTTP work (``search``, ``info``,
``serve``, ``api``, ``upload``, ``analytics``, ``auth login``) are
intentionally not invoked here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ytmcp.cli import app
from ytmcp.config import get_settings

runner = CliRunner()

# Expected top-level commands shown in ``--help``.
EXPECTED_COMMANDS = [
    "serve",
    "api",
    "info",
    "search",
    "upload",
    "analytics",
    "auth",
    "config",
    "version",
]

@pytest.fixture(autouse=True)
def _clean_settings_cache() -> None:
    """Keep the lru_cache'd settings from leaking between tests.

    ``get_settings`` is cached process-wide; tests that override env vars must
    start and end with a clean cache so they never contaminate other modules.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

# --------------------------------------------------------------------------- #
# version
# --------------------------------------------------------------------------- #
def test_version_prints_version_and_exit_zero() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output

# --------------------------------------------------------------------------- #
# --help
# --------------------------------------------------------------------------- #
def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in EXPECTED_COMMANDS:
        assert command in result.output

# --------------------------------------------------------------------------- #
# config (redaction)
# --------------------------------------------------------------------------- #
def test_config_prints_json() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, dict)
    # A few well-known keys should be present in the dumped settings.
    assert "auth_mode" in data
    assert "api_port" in data

def test_config_redacts_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YTMCP_OAUTH_CLIENT_SECRET", "rahasia")
    monkeypatch.setenv("YTMCP_OAUTH_REFRESH_TOKEN", "refresh-rahasia")
    monkeypatch.setenv("YTMCP_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()

    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0

    data = json.loads(result.output)
    assert data["oauth_client_secret"] == "***"
    assert data["oauth_refresh_token"] == "***"
    # The real secrets must never appear in the output.
    assert "rahasia" not in result.output

def test_config_leaves_unset_secrets_as_null() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["oauth_client_secret"] is None
    assert data["oauth_refresh_token"] is None

# --------------------------------------------------------------------------- #
# auth status
# --------------------------------------------------------------------------- #
def test_auth_status_reports_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate settings so no ambient cookie/oauth config changes the mode.
    monkeypatch.setenv("YTMCP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("YTMCP_OAUTH_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("YTMCP_COOKIE_FILE", raising=False)
    get_settings.cache_clear()

    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "Auth mode:" in result.output
    assert "none" in result.output

# --------------------------------------------------------------------------- #
# auth verify-cookies
# --------------------------------------------------------------------------- #
def _write_netscape_no_auth(path: Path) -> None:
    """Write a parseable Netscape cookie file that has no YouTube auth cookies."""
    content = (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tFALSE\t9999999999\tfoo\tbar\n"
    )
    path.write_text(content, encoding="utf-8")

def test_verify_cookies_without_auth_returns_exit_1(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    _write_netscape_no_auth(cookie_file)

    result = runner.invoke(app, ["auth", "verify-cookies", str(cookie_file)])
    assert result.exit_code == 1
    assert "authenticated: False" in result.output

def test_verify_cookies_with_auth_returns_exit_0(
    tmp_path: Path, netscape_cookies: Path
) -> None:
    result = runner.invoke(app, ["auth", "verify-cookies", str(netscape_cookies)])
    assert result.exit_code == 0
    assert "authenticated: True" in result.output
