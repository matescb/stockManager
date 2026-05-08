"""Per-workspace TrustedParts request budget tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Literal
from uuid import UUID


@dataclass(frozen=True)
class BudgetVerdict:
    allow: bool
    mode: Literal["live", "degraded", "blocked"]
    reason: str


# Soft thresholds: (rolling window seconds, parts searched in window).
SOFT_LIMITS = [(10, 50), (60, 150), (3600, 2000), (86400, 15000)]

# Hard thresholds: (rolling window seconds, parts searched in window).
HARD_LIMITS = [(10, 250), (60, 1000), (3600, 25000), (86400, 250000)]

_WINDOWS = tuple(window_seconds for window_seconds, _limit in HARD_LIMITS)


class BudgetTracker:
    """In-process rolling-window parts-count budget per workspace."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._events: dict[tuple[UUID, int], list[tuple[float, int]]] = {}

    def record(self, workspace_id: UUID, parts_count: int) -> None:
        self._validate_parts_count(parts_count)
        now = self._clock()
        for window_seconds in _WINDOWS:
            self._events.setdefault((workspace_id, window_seconds), []).append((now, parts_count))
        self._prune(workspace_id)

    def check(self, workspace_id: UUID, parts_count: int) -> BudgetVerdict:
        self._validate_parts_count(parts_count)
        self._prune(workspace_id)

        for window_seconds, limit in HARD_LIMITS:
            if self._window_total(workspace_id, window_seconds) + parts_count > limit:
                return BudgetVerdict(
                    allow=False,
                    mode="blocked",
                    reason=f"hard limit exceeded for {self._window_label(window_seconds)} window",
                )

        for window_seconds, limit in SOFT_LIMITS:
            if self._window_total(workspace_id, window_seconds) + parts_count > limit:
                return BudgetVerdict(
                    allow=True,
                    mode="degraded",
                    reason=f"soft limit exceeded for {self._window_label(window_seconds)} window",
                )

        return BudgetVerdict(
            allow=True,
            mode="live",
            reason="within TrustedParts parts-count budget",
        )

    def _prune(self, workspace_id: UUID) -> None:
        now = self._clock()
        for window_seconds in _WINDOWS:
            key = (workspace_id, window_seconds)
            events = self._events.get(key)
            if events is None:
                continue

            keep_after = now - window_seconds
            fresh_events = [
                (timestamp, count) for timestamp, count in events if timestamp >= keep_after
            ]
            if fresh_events:
                self._events[key] = fresh_events
            else:
                self._events.pop(key, None)

    def _window_total(self, workspace_id: UUID, window_seconds: int) -> int:
        events = self._events.get((workspace_id, window_seconds), [])
        return sum(count for _timestamp, count in events)

    @staticmethod
    def _validate_parts_count(parts_count: int) -> None:
        if parts_count <= 0:
            raise ValueError("parts_count must be positive")

    @staticmethod
    def _window_label(window_seconds: int) -> str:
        if window_seconds < 60:
            return f"{window_seconds}s"
        if window_seconds < 3600:
            return f"{window_seconds // 60}m"
        if window_seconds < 86400:
            return f"{window_seconds // 3600}h"
        return f"{window_seconds // 86400}d"


BUDGET = BudgetTracker()
"""Process-local budget singleton.

Production currently runs uvicorn with ``--workers 1``, so the in-memory
budget is shared by all requests in that process. If workers increase, a
future Redis ADR must replace this per-process store.
"""
