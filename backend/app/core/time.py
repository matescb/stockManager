"""Shared time utility (CQ-001 / issue #117).

Centralises ``datetime.now(timezone.utc)`` so we only have one callsite
to mock when freezegun-style time travel arrives, and so the SQLAlchemy
``default=utcnow`` reads as a clear "use the same one everyone else
does".

Keep this file intentionally tiny. No caching, no monkeypatch hooks —
freezegun substitutes ``datetime.now`` itself, which is the right
boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current time as a tz-aware UTC datetime."""
    return datetime.now(timezone.utc)
