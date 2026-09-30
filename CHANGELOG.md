# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-30

### Added
- **Core unofficial client** (`ytmcp.core`):
  - `YouTubeClient` — async HTTP with retry/backoff, Retry-After handling,
    mutation throttling, and InnerTube helpers.
  - Hybrid auth: OAuth 2.0 device flow + Netscape/JSON cookie support,
    resolved automatically via `AuthResolver`.
  - Services: channel, upload (resumable), metadata, playlist, comments,
    analytics, search.
  - Pydantic models for videos, channels, playlists, comments, analytics.
- **MCP server** exposing 20 tools (video, playlist, comment, channel,
  analytics) via stdio or HTTP transports.
- **REST API** (FastAPI) with 19 routes and auto-generated OpenAPI docs.
- **CLI** (Typer + Rich) with `serve`, `api`, `upload`, `search`, `info`,
  `analytics`, `auth`, `config`, and `version` commands.
- **Tests**: 24 unit tests covering auth parsing, models, HTTP retry logic,
  and response parsers.
- Documentation: `README`, `docs/AUTH.md`, `docs/ARCHITECTURE.md`,
  `docs/TOOLS.md`, and runnable examples.
- CI workflow (lint + tests) and MIT license.

### Security
- Auth token cache and cookie files are gitignored by default.

[Unreleased]: https://github.com/your-username/ytmcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/your-username/ytmcp/releases/tag/v0.1.0
