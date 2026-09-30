# Architecture

`ytmcp` is layered so the volatile, undocumented parts are isolated from the
stable public interface.

```
┌─────────────────────────────────────────────────────────┐
│                     AI Agent (LLM)                      │
└───────────────────────────┬─────────────────────────────┘
                            │ MCP (stdio / HTTP)
                            ▼
┌─────────────────────────────────────────────────────────┐
│  mcp/  ── server.py + tools/*  (20 tools)               │
│  api/  ── FastAPI REST (19 routes)                      │
│  cli.py ─ Typer commands                                │
└───────────────────────────┬─────────────────────────────┘
                            │ shared service calls
                            ▼
┌─────────────────────────────────────────────────────────┐
│  core/  — the unofficial client                         │
│   client.py    HTTP + retry + throttle + InnerTube      │
│   auth.py      hybrid OAuth / cookie resolver           │
│   channel.py   info, branding, subscribers              │
│   upload.py    resumable upload engine                  │
│   metadata.py  edit / visibility / delete               │
│   playlist.py  playlist CRUD                            │
│   comments.py  list / reply / moderate                  │
│   analytics.py stats + top videos                       │
│   search.py    search + channel listing                 │
│   models.py    Pydantic models                          │
└───────────────────────────┬─────────────────────────────┘
                            │ HTTPS
                            ▼
             YouTube internal endpoints (youtubei/v1, studio)
```

## Design principles

1. **Volatility isolation.** Only `core/` talks to YouTube's private
   endpoints. When YouTube changes, fixes stay local.
2. **One core, many faces.** The MCP server, REST API, and CLI all call the
   same services — no duplicated logic.
3. **Docstring-driven tools.** MCP tool schemas are generated from type hints
   and docstrings, so documentation and schema never drift.
4. **Defensive networking.** Every request goes through retry-with-backoff,
   Retry-After handling, and mutation throttling.
5. **Auth abstraction.** `AuthResolver` picks OAuth or cookies; services stay
   agnostic.

## Data flow example — upload

```
upload_video (MCP tool)
  └─ open_client(authenticated=True)     # start client + apply auth
      └─ UploadController.upload()
          ├─ _initiate()                 # create resumable session
          ├─ _send_chunks()              # PUT file in 1 MiB chunks
          └─ set_thumbnail()             # optional
  └─ UploadResult → JSON
```

## Extension points

- **New tool**: add a function in `mcp/tools/*_tools.py` and list it in that
  module's `register()`.
- **New service**: add a module in `core/`, export it from `core/__init__.py`.
- **New transport**: adjust `mcp/server.py::main`.
