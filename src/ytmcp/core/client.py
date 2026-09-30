"""Low-level HTTP client with retry, rate-limiting, and cookie support."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from ..config import Settings, get_settings
from .exceptions import RateLimitError, UpstreamError

logger = logging.getLogger(__name__)

YOUTUBE_BASE = "https://www.youtube.com"
STUDIO_BASE = "https://studio.youtube.com"
INNERTUBE_BASE = "https://www.youtube.com/youtubei/v1"
INNERTUBE_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"  # public web key
INNERTUBE_CLIENT_VERSION = "2.20240401.00.00"


class YouTubeClient:
    """Async HTTP client wrapping YouTube's internal endpoints.

    Handles retries with exponential backoff + jitter, basic rate-limiting,
    and (optionally) cookie-based session state.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._cookies: dict[str, str] = dict(cookies or {})
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last_request_at: float = 0.0

    # -- lifecycle ----------------------------------------------------------
    async def __aenter__(self) -> YouTubeClient:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is not None:
            return
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": YOUTUBE_BASE,
        }
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=self.settings.timeout,
            follow_redirects=True,
            http2=True,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("YouTubeClient not started; use 'async with' or call start().")
        return self._client

    # -- cookies ------------------------------------------------------------
    def set_cookies(self, cookies: dict[str, str]) -> None:
        self._cookies.update(cookies)

    @property
    def cookies(self) -> dict[str, str]:
        return dict(self._cookies)

    def _apply_cookies(self, kwargs: dict[str, Any]) -> None:
        if self._cookies:
            kwargs.setdefault("cookies", {}).update(self._cookies)

    # -- rate limiting ------------------------------------------------------
    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            elapsed = now - self._last_request_at
            delay = self.settings.rate_limit_delay
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request_at = asyncio.get_running_loop().time()

    # -- request core -------------------------------------------------------
    async def request(
        self,
        method: str,
        url: str,
        *,
        mutate: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """Perform an HTTP request with retry + throttling.

        Args:
            method: HTTP method.
            url: Absolute URL.
            mutate: When True, applies stricter throttling (state-changing call).
            **kwargs: Passed through to httpx.

        Returns:
            The :class:`httpx.Response`.

        Raises:
            RateLimitError: On HTTP 429 after exhausting retries.
            UpstreamError: On repeated 5xx / network failures.
        """
        self._apply_cookies(kwargs)
        last_exc: Exception | None = None

        for attempt in range(self.settings.max_retries + 1):
            if mutate:
                await self._throttle()
            try:
                resp = await self.client.request(method, url, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning("Request error (attempt %d): %s", attempt + 1, exc)
                await self._backoff(attempt)
                continue

            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                if attempt < self.settings.max_retries:
                    logger.warning("Rate limited; retrying after %ss", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                raise RateLimitError("YouTube rate limit reached.", retry_after=retry_after)

            if 500 <= resp.status_code < 600:
                last_exc = UpstreamError(
                    f"Upstream {resp.status_code}", status_code=resp.status_code
                )
                await self._backoff(attempt)
                continue

            return resp

        if isinstance(last_exc, RateLimitError):
            raise last_exc
        raise UpstreamError(f"Request failed after retries: {last_exc}")

    async def _backoff(self, attempt: int) -> None:
        base = min(2**attempt, 30)
        await asyncio.sleep(base + random.uniform(0, 0.5))

    # -- conveniences -------------------------------------------------------
    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("POST", url, mutate=True, **kw)

    async def put(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("PUT", url, mutate=True, **kw)

    async def delete(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("DELETE", url, mutate=True, **kw)

    # -- innertube helper ---------------------------------------------------
    def innertube_payload(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build a standard InnerTube request body."""
        payload: dict[str, Any] = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": INNERTUBE_CLIENT_VERSION,
                    "hl": "en",
                    "gl": "US",
                }
            }
        }
        if extra:
            payload.update(extra)
        return payload

    async def innertube(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        mutate: bool = False,
    ) -> dict[str, Any]:
        """Call an InnerTube endpoint and return parsed JSON."""
        url = f"{INNERTUBE_BASE}/{endpoint}?key={INNERTUBE_API_KEY}&prettyPrint=false"
        resp = await self.request(
            "POST", url, json=self.innertube_payload(payload), mutate=mutate
        )
        if resp.status_code >= 400:
            raise UpstreamError(
                f"InnerTube {endpoint} failed: {resp.status_code}", status_code=resp.status_code
            )
        return resp.json()


def _parse_retry_after(value: str | None) -> float:
    if not value:
        return 5.0
    try:
        return max(0.0, float(value))
    except ValueError:
        return 5.0
