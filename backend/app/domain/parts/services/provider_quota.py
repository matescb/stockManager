"""Quota and pacing for agent-path distributor calls (SM-005).

The DigiKey and Mouser allowances (DigiKey publishes 1000 Product
Information calls a day) are shared by everything that asks a provider
about a part: the UI lookup, scan import, refresh-from-provider, the
`provider-refresh` job — and now agents through `catalog_lookup`. An
agent that spends the day's allowance stops the owner's own work, so the
agent path is held to three rules the other callers are not:

* **Reserve.** When the provider's remaining count for the day is below
  `PROVIDER_AGENT_QUOTA_RESERVE`, agent tools refuse with
  `provider.quota_reserve_reached`. Nothing else consults the reserve.
* **Pacing.** Two live calls to one provider from one workspace are at
  least `PROVIDER_AGENT_MIN_SPACING_SECONDS` apart. The caller sleeps for
  the difference (at most that spacing) rather than being refused —
  DigiKey suspends an application for ten minutes when a per-second
  threshold is crossed, and one second of waiting is cheaper than that.
* **Reporting.** Every agent answer carries the `quota` object built by
  `snapshot()`.

Where "remaining" comes from
----------------------------
DigiKey returns the day's remainder in `X-RateLimit-Remaining`. The two
network seams in `providers/digikey.py` hand their response headers to
`note_remaining()` — a side channel, so the seams keep returning
`(status, body)` and the tests that stub them keep working. A value seen
today wins (`source: "header"`), less any calls this process has made
since. With no header today (Mouser never sends one; DigiKey before its
first call of the day) the answer is the configured daily limit minus the
calls this process has counted (`source: "local-count"`). That count
cannot see the `provider-refresh` job, which runs in its own cron
container — the header is the only number that can, which is why it wins.

Calls are counted per provider ACCOUNT (a hash of the client id or API
key), because the allowance belongs to the credential, not to the
workspace; pacing is per workspace and provider, as SM-005 states it.

Process-local, like `domain/sourcing/budget.py` and the provider cache:
prod runs one uvicorn worker. More workers need a shared store first.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date
from threading import Lock
from typing import Any, Callable, Literal, Mapping
from uuid import UUID

from app.core.time import utcnow

__all__ = [
    "QUOTA",
    "ProviderQuota",
    "QuotaReserveReached",
    "QuotaStatus",
    "account_key",
]

_REMAINING_HEADER = "x-ratelimit-remaining"

QuotaSource = Literal["header", "local-count"]


def account_key(credential: str | None) -> str:
    """A stable, non-reversible name for one provider credential."""
    digest = hashlib.sha256((credential or "").encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass(frozen=True)
class QuotaStatus:
    provider: str
    remaining_day: int | None
    reserve: int
    source: QuotaSource

    @property
    def below_reserve(self) -> bool:
        return self.remaining_day is not None and self.remaining_day < self.reserve


class QuotaReserveReached(Exception):
    """The agent path may not spend what is left of the day's allowance."""

    def __init__(self, status: QuotaStatus) -> None:
        super().__init__(
            f"{status.provider} has {status.remaining_day} calls left today "
            f"({status.source}), below the reserve of {status.reserve} kept "
            "for the owner's own lookups; try again tomorrow or ask the owner"
        )
        self.status = status


@dataclass
class _Account:
    day: date
    calls: int = 0
    header_remaining: int | None = None
    calls_since_header: int = 0


def _default_today() -> date:
    return utcnow().date()


def _settings():
    # Imported lazily: the provider modules import this one, and config
    # pulls in pydantic-settings at import time.
    from app.core.config import settings

    return settings()


def _parse_remaining(headers: Mapping[str, Any] | None) -> int | None:
    if not headers:
        return None
    raw = None
    for key, value in headers.items():
        if str(key).lower() == _REMAINING_HEADER:
            raw = value
            break
    try:
        value = int(str(raw).strip()) if raw is not None else None
    except ValueError:
        return None
    return value if value is not None and value >= 0 else None


class ProviderQuota:
    """Per-process quota ledger and pacer. See the module docstring."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], date] = _default_today,
    ) -> None:
        self.clock = clock
        self.sleep = sleep
        self.today = today
        self._lock = Lock()
        self._accounts: dict[tuple[str, str], _Account] = {}
        self._next_slot: dict[tuple[UUID, str], float] = {}

    def reset(self) -> None:
        with self._lock:
            self._accounts.clear()
            self._next_slot.clear()

    # ---- recording (every caller, via the provider seams) ---------------
    def note_call(self, provider: str, account: str) -> None:
        """One upstream call is about to be made with this credential."""
        with self._lock:
            acct = self._account(provider, account)
            acct.calls += 1
            if acct.header_remaining is not None:
                acct.calls_since_header += 1

    def note_remaining(
        self, provider: str, account: str, headers: Mapping[str, Any] | None
    ) -> None:
        """Record the provider's own remaining count, if it sent one."""
        remaining = _parse_remaining(headers)
        if remaining is None:
            return
        with self._lock:
            acct = self._account(provider, account)
            acct.header_remaining = remaining
            acct.calls_since_header = 0

    def calls_today(self, provider: str, account: str) -> int:
        with self._lock:
            return self._account(provider, account).calls

    # ---- the agent path -------------------------------------------------
    def status(self, provider: str, account: str) -> QuotaStatus:
        cfg = _settings()
        reserve = int(cfg.PROVIDER_AGENT_QUOTA_RESERVE)
        with self._lock:
            acct = self._account(provider, account)
            if acct.header_remaining is not None:
                remaining = max(0, acct.header_remaining - acct.calls_since_header)
                return QuotaStatus(provider, remaining, reserve, "header")
            limit = _daily_limit(cfg, provider)
            remaining = max(0, limit - acct.calls) if limit else None
            return QuotaStatus(provider, remaining, reserve, "local-count")

    def check_reserve(self, provider: str, account: str) -> QuotaStatus:
        """Raise `QuotaReserveReached` when an agent may not spend more."""
        status = self.status(provider, account)
        if status.below_reserve:
            raise QuotaReserveReached(status)
        return status

    def pace(self, workspace_id: UUID, provider: str) -> float:
        """Wait until this workspace may call *provider* again.

        The slot is claimed under the lock BEFORE sleeping, so two
        concurrent callers queue one spacing apart instead of both
        computing the same wait and firing together. Returns the seconds
        waited.
        """
        spacing = float(_settings().PROVIDER_AGENT_MIN_SPACING_SECONDS)
        key = (workspace_id, provider)
        with self._lock:
            now = self.clock()
            slot = max(now, self._next_slot.get(key, now))
            self._next_slot[key] = slot + spacing
        wait = slot - now
        if wait > 0:
            self.sleep(wait)
        return max(0.0, wait)

    def snapshot(self, provider: str, account: str, *, spent: int) -> dict[str, Any]:
        """The `quota` object every agent answer carries."""
        status = self.status(provider, account)
        return {
            "provider": provider,
            "spent": spent,
            "remaining_day": status.remaining_day,
            "reserve": status.reserve,
            "source": status.source,
        }

    # ---- internals ------------------------------------------------------
    def _account(self, provider: str, account: str) -> _Account:
        """The account's ledger for today. Caller holds the lock."""
        today = self.today()
        key = (provider, account)
        acct = self._accounts.get(key)
        if acct is None or acct.day != today:
            acct = _Account(day=today)
            self._accounts[key] = acct
        return acct


def _daily_limit(cfg, provider: str) -> int:
    if provider == "digikey":
        return int(cfg.PROVIDER_DAILY_LIMIT_DIGIKEY)
    if provider == "mouser":
        return int(cfg.PROVIDER_DAILY_LIMIT_MOUSER)
    return 0


QUOTA = ProviderQuota()
"""Process-local singleton (one uvicorn worker in prod)."""
