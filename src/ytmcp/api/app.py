"""FastAPI application exposing the unofficial YouTube API as REST."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import get_settings
from .routes import analytics, channel, comments, playlists, videos

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(
        title="ytmcp REST API",
        version="0.1.0",
        description=(
            "Unofficial YouTube REST API powering the ytmcp MCP server. "
            "Use responsibly — see the project README for the disclaimer."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(videos.router, prefix="/videos", tags=["videos"])
    app.include_router(playlists.router, prefix="/playlists", tags=["playlists"])
    app.include_router(comments.router, prefix="/comments", tags=["comments"])
    app.include_router(channel.router, prefix="/channel", tags=["channel"])
    app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "auth_mode": settings.auth_mode}

    return app


app = create_app()


def main() -> None:
    """Run the API with uvicorn (``ytmcp-api`` entry point)."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
