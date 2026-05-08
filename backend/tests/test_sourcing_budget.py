from __future__ import annotations

import importlib
from uuid import uuid4

from app.domain.sourcing import budget as budget_module
from app.domain.sourcing.budget import BUDGET, BudgetTracker, BudgetVerdict


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_below_soft_returns_live():
    tracker = BudgetTracker(clock=FakeClock())
    ws = uuid4()

    verdict = tracker.check(ws, 50)

    assert verdict == BudgetVerdict(
        allow=True,
        mode="live",
        reason="within TrustedParts parts-count budget",
    )


def test_above_soft_below_hard_returns_degraded():
    tracker = BudgetTracker(clock=FakeClock())
    ws = uuid4()
    tracker.record(ws, 50)

    verdict = tracker.check(ws, 1)

    assert verdict.allow is True
    assert verdict.mode == "degraded"
    assert verdict.reason == "soft limit exceeded for 10s window"


def test_above_hard_returns_blocked():
    tracker = BudgetTracker(clock=FakeClock())
    ws = uuid4()
    tracker.record(ws, 250)

    verdict = tracker.check(ws, 1)

    assert verdict.allow is False
    assert verdict.mode == "blocked"
    assert verdict.reason == "hard limit exceeded for 10s window"


def test_counter_increments_by_parts_count_not_one():
    bulk_tracker = BudgetTracker(clock=FakeClock())
    repeated_tracker = BudgetTracker(clock=FakeClock())
    ws = uuid4()

    bulk_tracker.record(ws, 50)
    for _index in range(50):
        repeated_tracker.record(ws, 1)

    assert bulk_tracker.check(ws, 1).mode == repeated_tracker.check(ws, 1).mode == "degraded"
    assert bulk_tracker.check(ws, 201).mode == repeated_tracker.check(ws, 201).mode == "blocked"


def test_window_expiry_with_fake_clock():
    clock = FakeClock()
    tracker = BudgetTracker(clock=clock)
    ws = uuid4()
    tracker.record(ws, 50)
    clock.advance(10.1)

    tracker._prune(ws)

    assert tracker.check(ws, 1).mode == "live"


def test_two_workspaces_independent():
    tracker = BudgetTracker(clock=FakeClock())
    loaded_ws = uuid4()
    quiet_ws = uuid4()
    tracker.record(loaded_ws, 250)

    assert tracker.check(loaded_ws, 1).mode == "blocked"
    assert tracker.check(quiet_ws, 50).mode == "live"


def test_singleton_identity():
    reimported = importlib.import_module("app.domain.sourcing.budget")

    assert BUDGET is budget_module.BUDGET
    assert BUDGET is reimported.BUDGET
