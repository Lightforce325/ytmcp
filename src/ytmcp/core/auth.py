"""Hybrid authentication: OAuth 2.0 and browser-cookie based auth.

The :class:`AuthResolver` picks the best available method automatically
(``auto`` mode) or honours an explicit choice (``oauth`` / ``cookie``).

Supported cookie formats
------------------------
* Netscape ``cookies.txt`` (as exported by browser extensions / yt-dlp)
* JSON array (as exported by "EditThisCookie" / Playwright ``storage_state``)

OAuth
-----
Implements the Google "device" and "refresh token" flows using only ``httpx``
(no ``google-auth`` dependency required).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from .client import YouTubeClient
from .exceptions import AuthError, AuthRequiredError
from .models import AuthContext

logger = logging.getLogger(__name__)

GOOGLE_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "https://www.googleapis.com/auth/youtube https://www.googleapis.com/auth/youtube.force-ssl"


# --------------------------------------------------------------------------- #
# Cookie handling
# --------------------------------------------------------------------------- #
def load_cookie_file(path: str | Path) -> dict[str, str]:
    """Load cookies from a Netscape or JSON cookie file.

    Args:
        path: Path to the cookie file.

    Returns:
        Mapping of cookie name -> value.

    Raises:
        AuthError: If the file is missing or unparseable.
    """
    p = Path(path)
    if not p.exists():
        raise AuthError(f"Cookie file not found: {p}")

    text = p.read_text(encoding="utf-8", errors="ignore").strip()

    # JSON format (array of objects, or Playwright storage_state)
    if text.startswith("{") or text.startswith("["):
        return _load_json_cookies(text)

    # Netscape cookies.txt format
    return _load_netscape_cookies(text)


def _load_json_cookies(text: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AuthError(f"Invalid JSON cookie file: {exc}") from exc

    if isinstance(data, dict) and "cookies" in data:  # Playwright storage_state
        data = data["cookies"]

    cookies: dict[str, str] = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "name" in item and "value" in item:
                cookies[item["name"]] = item["value"]
    elif isinstance(data, dict):
        cookies = {str(k): str(v) for k, v in data.items()}
    if not cookies:
        raise AuthError("No cookies found in JSON file.")
    return cookies


def _load_netscape_cookies(text: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    if not cookies:
        raise AuthError("No cookies found in Netscape cookie file.")
    return cookies


def has_auth_cookies(cookies: dict[str, str]) -> bool:
    """Return True if the cookie set looks like an authenticated YouTube session."""
    return "SAPISID" in cookies or "__Secure-3PAPISID" in cookies or "SID" in cookies


# --------------------------------------------------------------------------- #
# OAuth
# --------------------------------------------------------------------------- #
class OAuthProvider:
    """Google OAuth 2.0 provider (device flow + refresh)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _require_client(self) -> tuple[str, str]:
        if not self.settings.oauth_client_id or not self.settings.oauth_client_secret:
            raise AuthRequiredError(
                "OAuth client id/secret not configured. Set YTMCP_OAUTH_CLIENT_ID and "
                "YTMCP_OAUTH_CLIENT_SECRET, or use cookie auth instead."
            )
        return self.settings.oauth_client_id, self.settings.oauth_client_secret

    async def start_device_flow(self, client: YouTubeClient) -> dict[str, Any]:
        """Begin the OAuth device flow; returns device_code + user_code."""
        cid, _ = self._require_client()
        resp = await client.post(
            GOOGLE_DEVICE_CODE_URL,
            data={"client_id": cid, "scope": GOOGLE_SCOPES},
        )
        if resp.status_code != 200:
            raise AuthError(f"Device flow init failed: {resp.status_code} {resp.text}")
        return resp.json()

    async def poll_device_token(
        self, client: YouTubeClient, device_code: str, interval: int = 5, timeout: float = 300
    ) -> dict[str, Any]:
        """Poll the token endpoint until the user authorises the device."""
        cid, secret = self._require_client()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": cid,
                    "client_secret": secret,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            data = resp.json()
            if resp.status_code == 200:
                self._save_token(data)
                return data
            error = data.get("error")
            if error in {"authorization_pending", "slow_down"}:
                await _sleep(interval)
                continue
            raise AuthError(f"Device token polling failed: {error} {data}")
        raise AuthError("Device flow timed out.")

    async def refresh(self, client: YouTubeClient) -> dict[str, Any]:
        """Refresh the access token using the cached/setting refresh token."""
        cid, secret = self._require_client()
        refresh_token = self.settings.oauth_refresh_token or self._load_cached().get(
            "refresh_token"
        )
        if not refresh_token:
            raise AuthRequiredError("No refresh token available for OAuth.")
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": cid,
                "client_secret": secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            raise AuthError(f"Token refresh failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if "refresh_token" not in data:
            data["refresh_token"] = refresh_token
        self._save_token(data)
        return data

    # -- token cache --------------------------------------------------------
    def _save_token(self, data: dict[str, Any]) -> None:
        data = dict(data)
        data["_saved_at"] = time.time()
        self.settings.token_cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_cached(self) -> dict[str, Any]:
        path = self.settings.token_cache_path
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def cached_access_token(self) -> str | None:
        data = self._load_cached()
        token = data.get("access_token")
        saved_at = data.get("_saved_at", 0)
        expires_in = data.get("expires_in", 0)
        if token and (time.time() - saved_at) < (expires_in - 60):
            return token
        return None


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #
class AuthResolver:
    """Chooses and applies the appropriate authentication strategy."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.oauth = OAuthProvider(self.settings)

    def resolve(self) -> AuthContext:
        """Determine which auth mode is available *without* mutating state."""
        mode = self.settings.auth_mode
        if mode == "oauth" or (
            mode == "auto"
            and (
                self.settings.oauth_refresh_token
                or self.settings.token_cache_path.exists()
            )
        ):
            return AuthContext(mode="oauth", is_valid=True)
        if mode == "cookie" or (mode == "auto" and self.settings.cookie_file):
            return AuthContext(mode="cookie", is_valid=True)
        return AuthContext(mode="none", is_valid=False)

    async def apply(self, client: YouTubeClient) -> AuthContext:
        """Apply resolved credentials to the given client.

        Returns:
            The applied :class:`AuthContext`.

        Raises:
            AuthRequiredError: If no auth method is configured/available.
        """
        ctx = self.resolve()
        if ctx.mode == "cookie":
            assert self.settings.cookie_file is not None
            cookies = load_cookie_file(self.settings.cookie_file)
            if not has_auth_cookies(cookies):
                logger.warning("Cookie file loaded but no YouTube auth cookies detected.")
            client.set_cookies(cookies)
            ctx.is_valid = True
            return ctx

        if ctx.mode == "oauth":
            token = self.oauth.cached_access_token()
            if not token:
                data = await self.oauth.refresh(client)
                token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                ctx.expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
            if not token:
                raise AuthError("OAuth refresh returned no access token.")
            await client.start()
            client.client.headers["Authorization"] = f"Bearer {token}"
            ctx.is_valid = True
            return ctx

        raise AuthRequiredError(
            "No authentication configured. Provide YTMCP_COOKIE_FILE or OAuth credentials. "
            "See docs/AUTH.md."
        )


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
