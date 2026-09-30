"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytmcp.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """A test Settings instance with an isolated data dir."""
    return Settings(
        data_dir=tmp_path / "data",
        cookie_file=None,
        oauth_refresh_token=None,
        rate_limit_delay=0.0,
        max_retries=1,
    )


@pytest.fixture
def netscape_cookies(tmp_path: Path) -> Path:
    """Write a Netscape-style cookies.txt and return its path."""
    content = (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSAPISID\tabc123\n"
        ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSID\tsid456\n"
    )
    path = tmp_path / "cookies.txt"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def json_cookies(tmp_path: Path) -> Path:
    """Write a JSON cookie export and return its path."""
    import json

    data = [
        {"name": "SAPISID", "value": "xyz", "domain": ".youtube.com"},
        {"name": "LOGIN_INFO", "value": "info", "domain": ".youtube.com"},
    ]
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path
