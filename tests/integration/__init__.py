"""Integration tests (require real credentials; opt-in).

Run explicitly with::

    YTMCP_RUN_INTEGRATION=1 uv run pytest tests/integration -m integration

These are skipped by default because they hit the live YouTube API and need
valid authentication.
"""
