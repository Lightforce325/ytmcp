"""Allow ``python -m ytmcp`` to launch the MCP server."""

from __future__ import annotations

from .mcp.server import main

if __name__ == "__main__":
    main()
