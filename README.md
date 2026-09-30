# ytmcp

> **Unofficial YouTube API + MCP server** — let AI agents control a YouTube channel end-to-end.

[![CI](https://github.com/Lightforce325/ytmcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Lightforce325/ytmcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)](https://modelcontextprotocol.io)

`ytmcp` is a Python library, REST API, CLI, and **[Model Context Protocol](https://modelcontextprotocol.io)** server that wraps an *unofficial* YouTube client. It lets AI agents (Claude Desktop, Cursor, custom agents…) upload videos, edit metadata, manage playlists, moderate comments, and read analytics — all through MCP tools.

---

## ⚠️ Disclaimer

This project is provided **for educational and research purposes only**.

- It uses **reverse-engineered, undocumented** YouTube endpoints that may change or break at any time.
- Automating YouTube may **violate YouTube's Terms of Service**.
- Using this software **can result in account suspension or termination**.
- The authors accept **no liability** for any consequences arising from its use.

**Use at your own risk, ideally with a test account.** You are solely responsible for complying with all applicable terms and laws.

---

## ✨ Features

| Domain | Capabilities |
|--------|-------------|
| **Video** | upload (resumable), update metadata, set visibility, set thumbnail, delete, get info |
| **Playlist** | create, list, add/remove videos, delete |
| **Comments** | list, reply, moderate (approve/hold/reject) |
| **Channel** | get info, subscriber count, update branding/links |
| **Analytics** | views, watch-time, subscriber growth, top videos |
| **Search** | search videos, list channel uploads |

Three interfaces share one core:

1. 🐍 **Python library** — `from ytmcp.core import YouTubeClient`
2. 🤖 **MCP server** — `ytmcp serve` (stdio / HTTP)
3. 🌐 **REST API** — `ytmcp api` (FastAPI, auto docs at `/docs`)

---

## 🔐 Authentication (hybrid)

`ytmcp` supports **two** auth methods and picks automatically (`auth_mode = auto`):

### Option A — Browser cookies (fastest)

1. Export cookies for `youtube.com` with a browser extension (e.g. *Get cookies.txt*) or `yt-dlp --cookies-from-browser chrome --cookies cookies.txt`.
2. Point ytmcp at the file:

```bash
export YTMCP_AUTH_MODE=cookie
export YTMCP_COOKIE_FILE=/path/to/cookies.txt
uv run ytmcp auth verify-cookies /path/to/cookies.txt
```

Supports **Netscape** `cookies.txt` and **JSON** exports (including Playwright `storage_state`).

### Option B — OAuth 2.0 (device flow)

1. Create an OAuth client (type **TVs and Limited Input devices**) in [Google Cloud Console](https://console.cloud.google.com/).
2. Configure and log in:

```bash
export YTMCP_AUTH_MODE=oauth
export YTMCP_OAUTH_CLIENT_ID=...
export YTMCP_OAUTH_CLIENT_SECRET=...
uv run ytmcp auth login
```

Tokens are cached in `~/.ytmcp/oauth_token.json` and refreshed automatically.

Check status any time:

```bash
uv run ytmcp auth status
```

---

## 🚀 Quick start

```bash
# 1. Install
git clone https://github.com/Lightforce325/ytmcp.git
cd ytmcp
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. Configure auth (see above)
cp .env.example .env  # then edit

# 3. Run the MCP server
uv run ytmcp serve

# or the REST API
uv run ytmcp api     # http://127.0.0.1:8765/docs
```

---

## 🤖 Connect to Claude Desktop (MCP)

Add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ytmcp": {
      "command": "uv",
      "args": [
        "--directory", "/ABSOLUTE/PATH/TO/ytmcp",
        "run", "ytmcp", "serve"
      ],
      "env": {
        "YTMCP_AUTH_MODE": "cookie",
        "YTMCP_COOKIE_FILE": "/ABSOLUTE/PATH/TO/cookies.txt"
      }
    }
  }
}
```

Restart Claude Desktop — the agent will now see **20 tools** for controlling your channel.

See [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json) and [`docs/TOOLS.md`](docs/TOOLS.md).

---

## 🧰 MCP tools

| Tool | Auth | Description |
|------|:----:|-------------|
| `upload_video` | ✅ | Upload a video file with metadata |
| `update_video_metadata` | ✅ | Edit title/description/tags/category |
| `set_video_visibility` | ✅ | public / unlisted / private / scheduled |
| `delete_video` | ✅ | Permanently delete a video |
| `get_video_info` | — | Fetch video details |
| `search_videos` | — | Search YouTube |
| `list_channel_videos` | — | List a channel's uploads |
| `create_playlist` | ✅ | Create a playlist |
| `list_playlists` | — | List channel playlists |
| `add_video_to_playlist` | ✅ | Add a video to a playlist |
| `remove_video_from_playlist` | ✅ | Remove a video from a playlist |
| `delete_playlist` | ✅ | Delete a playlist |
| `list_comments` | — | List video comments |
| `reply_to_comment` | ✅ | Reply to a comment |
| `moderate_comment` | ✅ | Approve / hold / reject a comment |
| `get_channel_info` | — | Channel metadata |
| `get_subscriber_count` | — | Subscriber count |
| `update_channel_branding` | ✅ | Banner / avatar / links |
| `get_analytics` | ✅ | Views, watch-time, growth |
| `get_top_videos` | — | Most-viewed videos |

---

## 🧩 Example agent workflow

> "Upload `intro.mp4` as a private video titled *Hello World*, add it to my *Demos* playlist, then reply to the latest comment."

The agent chains: `upload_video` → `list_playlists` → `add_video_to_playlist` → `list_comments` → `reply_to_comment`. See [`examples/agent_batch_upload.py`](examples/agent_batch_upload.py).

---

## 🏗️ Architecture

```
AI Agent ──MCP──► mcp/server.py ──► core/* ──► YouTube endpoints
                     api/app.py  ──►
                     cli.py      ──►
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full breakdown.

---

## 🧪 Development

```bash
uv pip install -e ".[dev]"    # install dev dependencies
uv run ruff check .           # lint
uv run ruff format .          # format
uv run mypy src               # type-check
```

### Testing

The suite lives in `tests/` and runs on [`pytest`](https://docs.pytest.org/)
with [`pytest-asyncio`](https://pytest-asyncio.readthedocs.io/) (`asyncio_mode = "auto"`)
and [`respx`](https://lundberg.github.io/respx/) for HTTP mocking — no network
access or credentials are needed for the default run.

```bash
# Run the whole suite (unit tests + integration tests, which skip by default)
uv run pytest

# Run only the unit tests
uv run pytest tests/unit

# Run a single file / test
uv run pytest tests/unit/test_upload.py -q
uv run pytest tests/unit/test_config.py::test_settings_defaults -q
```

#### Coverage

Coverage is configured with [`pytest-cov`](https://pytest-cov.readthedocs.io/)
and [`coverage.py`](https://coverage.readthedocs.io/). Configuration lives in
`pyproject.toml` under `[tool.coverage.run]` (source = `src/ytmcp`, branch
coverage enabled) and `[tool.coverage.report]`.

```bash
# Terminal report highlighting missing lines
uv run pytest --cov=src/ytmcp --cov-report=term-missing

# HTML report (written to htmlcov/, git-ignored)
uv run pytest --cov=src/ytmcp --cov-report=html
open htmlcov/index.html

# Fail the run if total coverage drops below a threshold
uv run pytest --cov=src/ytmcp --cov-report=term-missing --cov-fail-under=70
```

#### Integration tests (opt-in)

End-to-end multi-service flows live in `tests/integration/`. They are marked
`integration` and **skipped by default**; opt in with the environment variable:

```bash
# Run everything, including integration tests
YTMCP_RUN_INTEGRATION=1 uv run pytest -m integration

# Run only the integration marker
YTMCP_RUN_INTEGRATION=1 uv run pytest tests/integration -m integration
```

#### Quality gates

```bash
uv run pytest                                  # tests
uv run pytest --cov=src/ytmcp --cov-report=term-missing  # tests + coverage
uv run ruff check .                            # lint
uv run mypy src                                # type-check
```

---

## 📄 License

[MIT](LICENSE) — provided as-is, without warranty. See the disclaimer above.
