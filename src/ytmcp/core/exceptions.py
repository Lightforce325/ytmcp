"""Custom exceptions for ytmcp."""

from __future__ import annotations


class YtmcpError(Exception):
    """Base exception for all ytmcp errors."""


class AuthError(YtmcpError):
    """Raised when authentication is missing, invalid, or expired."""


class AuthRequiredError(AuthError):
    """Raised when an operation requires auth but none is available."""


class RateLimitError(YtmcpError):
    """Raised when the upstream service rate-limits us."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class UploadError(YtmcpError):
    """Raised when a video upload fails."""


class NotFoundError(YtmcpError):
    """Raised when a requested resource does not exist."""


class UpstreamError(YtmcpError):
    """Raised when YouTube returns an unexpected error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
