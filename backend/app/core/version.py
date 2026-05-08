"""Build version helpers."""

from __future__ import annotations

import os


def git_sha() -> str:
    """Return the deployed git SHA, or ``dev`` for local/test runs."""
    return os.environ.get("GIT_SHA") or "dev"
