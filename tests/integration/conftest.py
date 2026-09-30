"""Integration-test configuration.

End-to-end flows that exercise several services together are **opt-in**.
By default every test in this directory is skipped. To run them::

    YTMCP_RUN_INTEGRATION=1 uv run pytest tests/integration -m integration

All network calls are still mocked with ``respx``; the env flag only gates
whether the flows execute at all (so CI stays green without touching the
decision to run them).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

RUN_INTEGRATION = os.environ.get("YTMCP_RUN_INTEGRATION") == "1"

SKIP_REASON = (
    "integration tests are opt-in; set YTMCP_RUN_INTEGRATION=1 to run them"
)

# Only items collected from this directory may be gated by this conftest.
_INTEGRATION_DIR = Path(__file__).resolve().parent


def _is_integration_item(item: pytest.Item) -> bool:
    """Return True when *item* belongs to the integration test directory."""
    try:
        item_path = Path(str(item.fspath)).resolve()
    except (AttributeError, OSError):
        return False
    return _INTEGRATION_DIR in item_path.parents


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip every integration test unless the opt-in env flag is set.

    NOTE: a sub-directory ``conftest.py`` hook receives **all** collected
    items for the whole session, not just those in its own directory.  We
    therefore filter to items under this directory so unit tests in
    ``tests/unit`` are never accidentally skipped.
    """
    if RUN_INTEGRATION:
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if _is_integration_item(item):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _gate_integration(request: pytest.FixtureRequest) -> None:
    """Belt-and-braces gate: also skip at run time if the flag is absent."""
    if not RUN_INTEGRATION and _is_integration_item(request.node):
        pytest.skip(SKIP_REASON)
