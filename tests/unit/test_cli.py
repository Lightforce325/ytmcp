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

import ytmcp
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

# Secrets that ``config`` must always redact.
SECRET_KEYS = ("oauth_client_secret", "oauth_refresh_token")

@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Isolate ``Settings`` so no ambient env/config leaks between tests.

    ``get_settings`` is cached process-wide, so the cache is cleared before and
    after every test. ``YTMCP_DATA_DIR`` is pinned to a tmp path (and the ambient
    ``YTMCP_*`` secrets are removed) so ``Settings()`` never reads the real
    ``~/.ytmcp`` or an operator's ``.env``.
    """
    for var in (
        "YTMCP_DATA_DIR",
        "YTMCP_COOKIE_FILE",
        "YTMCP_OAUTH_CLIENT_SECRET",
        "YTMCP_OAUTH_REFRESH_TOKEN",
        "YTMCP_OAUTH_CLIENT_ID",
        "YTMCP_AUTH_MODE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("YTMCP_DATA_DIR", str(tmp_path / "data"))
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

def test_version_matches_package_dunder_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert ytmcp.__version__ in result.output

# --------------------------------------------------------------------------- #
# --help
# --------------------------------------------------------------------------- #
def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in EXPECTED_COMMANDS:
        assert command in result.output

def test_help_without_args_shows_usage() -> None:
    # ``no_args_is_help=True`` -> bare invocation prints help (exit code 0 or 2
    # depending on Click version, so only assert on the rendered commands).
    result = runner.invoke(app, [])
    for command in EXPECTED_COMMANDS:
        assert command in result.output

def test_auth_subcommand_help() -> None:
    result = runner.invoke(app, ["auth", "--help"])
    assert result.exit_code == 0
    for command in ("status", "login", "verify-cookies"):
        assert command in result.output

# --------------------------------------------------------------------------- #
# config (JSON + redaction)
# --------------------------------------------------------------------------- #
def test_config_prints_json() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, dict)
    # A few well-known keys should be present in the dumped settings.
    assert "auth_mode" in data
    assert "api_port" in data

def test_config_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YTMCP_OAUTH_CLIENT_SECRET", "rahasia")
    monkeypatch.setenv("YTMCP_OAUTH_REFRESH_TOKEN", "refresh-rahasia")
    get_settings.cache_clear()

    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0

    data = json.loads(result.output)
    assert data["oauth_client_secret"] == "***"
    assert data["oauth_refresh_token"] == "***"
    # The real secrets must never appear in the output.
    assert "rahasia" not in result.output

def test_config_redacts_each_secret_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the *set* secret is redacted; the other stays ``null``."""
    monkeypatch.setenv("YTMCP_OAUTH_CLIENT_SECRET", "only-client-secret")
    get_settings.cache_clear()

    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0

    data = json.loads(result.output)
    assert data["oauth_client_secret"] == "***"
    assert data["oauth_refresh_token"] is None
    assert "only-client-secret" not in result.output

def test_config_leaves_unset_secrets_as_null() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    for key in SECRET_KEYS:
        assert data[key] is None

# --------------------------------------------------------------------------- #
# auth status
# --------------------------------------------------------------------------- #
def test_auth_status_reports_none() -> None:
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "Auth mode:" in result.output
    assert "none" in result.output

def test_auth_status_reports_cookie_mode(netscape_cookies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YTMCP_COOKIE_FILE", str(netscape_cookies))
    get_settings.cache_clear()

    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "cookie" in result.output
    assert "valid=True" in result.output

def test_auth_status_reports_oauth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YTMCP_OAUTH_REFRESH_TOKEN", "refresh-token")
    get_settings.cache_clear()

    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "oauth" in result.output

# --------------------------------------------------------------------------- #
# auth verify-cookies
# --------------------------------------------------------------------------- #
def _write_netscape(path: Path, cookie_names: list[str]) -> Path:
    """Write a parseable Netscape cookie file containing *cookie_names*."""
    lines = ["# Netscape HTTP Cookie File"]
    for name in cookie_names:
        lines.append(f".youtube.com\tTRUE\t/\tTRUE\t9999999999\t{name}\tvalue-{name}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

def test_verify_cookies_without_auth_returns_exit_1(tmp_path: Path) -> None:
    cookie_file = _write_netscape(tmp_path / "cookies.txt", ["foo", "bar"])

    result = runner.invoke(app, ["auth", "verify-cookies", str(cookie_file)])
    assert result.exit_code == 1
    assert "authenticated: False" in result.output

def test_verify_cookies_empty_jar_returns_exit_1(tmp_path: Path) -> None:
    # A file with only a header line yields *no* cookies at all -> AuthError.
    cookie_file = tmp_path / "empty.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    result = runner.invoke(app, ["auth", "verify-cookies", str(cookie_file)])
    assert result.exit_code == 1

@pytest.mark.parametrize("name", ["SAPISID", "__Secure-3PAPISID", "SID"])
def test_verify_cookies_accepts_each_auth_cookie(tmp_path: Path, name: str) -> None:
    cookie_file = _write_netscape(tmp_path / "cookies.txt", [name])

    result = runner.invoke(app, ["auth", "verify-cookies", str(cookie_file)])
    assert result.exit_code == 0
    assert "authenticated: True" in result.output

def test_verify_cookies_with_auth_returns_exit_0(netscape_cookies: Path) -> None:
    result = runner.invoke(app, ["auth", "verify-cookies", str(netscape_cookies)])
    assert result.exit_code == 0
    assert "authenticated: True" in result.output

def test_verify_cookies_json_export_without_auth_returns_exit_1(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(
        json.dumps([{"name": "LOGIN_INFO", "value": "info"}]), encoding="utf-8"
    )

    result = runner.invoke(app, ["auth", "verify-cookies", str(cookie_file)])
    assert result.exit_code == 1
    assert "authenticated: False" in result.output

def test_verify_cookies_missing_file_returns_exit_1(tmp_path: Path) -> None:
    # A missing jar surfaces as ``AuthError`` (exit 1); the CLI does not catch it,
    # so nothing is written to stdout -- assert on the exception, not the output.
    result = runner.invoke(app, ["auth", "verify-cookies", str(tmp_path / "nope.txt")])
    assert result.exit_code == 1
    assert result.exception is not None
    assert "Cookie file not found" in str(result.exception)
