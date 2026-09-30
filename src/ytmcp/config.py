"""Configuration and settings for ytmcp.

Settings are loaded from environment variables (prefixed with ``YTMCP_``)
and/or a local ``.env`` file. See ``.env.example`` for the full list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AuthMode = Literal["auto", "oauth", "cookie"]


class Settings(BaseSettings):
    """Runtime settings for ytmcp."""

    model_config = SettingsConfigDict(
        env_prefix="YTMCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Auth ---------------------------------------------------------------
    auth_mode: AuthMode = Field(
        default="auto",
        description="Which auth provider to use: auto | oauth | cookie.",
    )
    oauth_client_id: str | None = Field(default=None)
    oauth_client_secret: str | None = Field(default=None)
    oauth_refresh_token: str | None = Field(default=None)
    cookie_file: Path | None = Field(
        default=None,
        description="Path to a Netscape/JSON cookie jar exported from a browser.",
    )

    # --- HTTP client --------------------------------------------------------
    timeout: float = Field(default=30.0)
    max_retries: int = Field(default=3)
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    )
    rate_limit_delay: float = Field(
        default=0.5,
        description="Base delay (seconds) between mutating requests.",
    )

    # --- Paths --------------------------------------------------------------
    data_dir: Path = Field(default=Path.home() / ".ytmcp")

    # --- Server -------------------------------------------------------------
    mcp_transport: Literal["stdio", "http"] = Field(default="stdio")
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8765)
    log_level: str = Field(default="INFO")

    @field_validator("data_dir", mode="after")
    @classmethod
    def _ensure_data_dir(cls, v: Path) -> Path:
        v.mkdir(parents=True, exist_ok=True)
        return v

    @property
    def token_cache_path(self) -> Path:
        """Where OAuth tokens are cached on disk."""
        return self.data_dir / "oauth_token.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
