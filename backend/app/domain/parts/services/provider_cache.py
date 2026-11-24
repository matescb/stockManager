"""TTL result cache + circuit breaker for provider MPN lookups.

Goals
-----
* Avoid burning upstream API quota on repeated lookups for the same MPN
  (e.g. scan-to-import batch, operator refreshing the same part repeatedly).
* Fail fast when a provider's upstream is clearly down rather than stalling
  the only uvicorn worker for 8 s × N consecutive calls.

Design notes
------------
Cache keying
    `(provider.name, mpn.strip().lower())` — two workspaces using the same
    provider share the same cache hit.  Both DigiKey and Mouser return
    public catalog data; nothing in the result is tied to the workspace's
    API key.  Pricing is a snapshot from the upstream catalog, not a
    workspace-scoped price list.

What is cached
    Only positive `{"found": True, ...}` results — failure responses are
    short-lived and re-trying after a minute is acceptable.  Negative hits
    from a real `{"found": False, "message": "no match for MPN"}` are also
    cached with a shorter TTL so we don't hammer the API on a typo either.

Circuit breaker
    Five consecutive failures → breaker opens for 60 s.  While open, calls
    raise `ProviderUpstreamError` without hitting the network.  The counter
    resets on any success.

    Per-provider, per-process state (in-memory dict).  Not persisted across
    restarts (CLAUDE.md hard constraint: `--workers 1` in prod).

Suspension (SM-005)
    DigiKey answers a burst over its per-second threshold with HTTP 403 and
    refuses the application for ten minutes.  The shared retry transport
    does not retry 403 (ADR-0023), and before this every 403 read as a
    plain failure — or, from the keyword fallback, as "no match".  A
    provider result carrying ``suspended: True`` now opens that provider's
    breaker for `_SUSPEND_SEC`, and every call in that window raises
    `ProviderSuspendedError` (a `ProviderUpstreamError`, so existing
    handlers are unchanged) whose message says "suspended" and how long.
    Cache hits are still served: they cost the provider nothing.

No new runtime dependencies.  The TTL LRU cache is hand-rolled because
`cachetools` is not in pyproject.toml and we should not add dependencies for
a ~20-line data structure.
"""
from __future__ import annotations

import math
import time
from collections import OrderedDict
from datetime import datetime
from typing import TYPE_CHECKING

from app.core.time import utcnow
from app.domain.parts.providers.base import ProviderUpstreamError
from app.domain.provider_errors import ProviderSuspendedError

if TYPE_CHECKING:
    from app.domain.parts.providers.base import MpnLookupResult, PartsProvider


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How long to keep a positive ("found": True) result in memory.
_HIT_TTL_SEC: float = 86_400.0  # 24 h

# How long to suppress a negative ("found": False) result.  Shorter so that
# a newly stocked part or a typo fix doesn't take a full day to re-check.
_MISS_TTL_SEC: float = 300.0  # 5 min

# Maximum number of entries across all providers.
_MAX_ENTRIES: int = 512

# Circuit-breaker tuning.
_CB_FAIL_THRESHOLD: int = 5      # consecutive failures before opening
_CB_OPEN_SEC: float = 60.0       # seconds the breaker stays open

# DigiKey's documented burst suspension: "403 Forbidden" for ten minutes.
_SUSPEND_SEC: float = 600.0

# Monotonic clock for TTLs and the breaker.  A module attribute so tests
# can substitute a fake clock instead of sleeping through ten minutes.
_clock = time.monotonic


# ---------------------------------------------------------------------------
# Internal: tiny TTL-LRU cache (no external deps)
# ---------------------------------------------------------------------------

class _TtlLruCache:
    """Thread-unsafe TTL-capped LRU cache.

    Eviction policy: when max size is reached, the least-recently-used entry
    is dropped first.  Entries that have exceeded their TTL are lazily evicted
    on every read.

    This is intentionally minimal — it only needs to work with the GIL and
    the synchronous (non-async) provider calls.
    """

    def __init__(self, maxsize: int, default_ttl: float) -> None:
        self._maxsize = maxsize
        self._default_ttl = default_ttl
        # Ordered by insertion/access order (LRU at the left).
        self._store: OrderedDict[str, tuple[object, float, datetime]] = OrderedDict()

    def _make_key(self, provider_name: str, mpn: str) -> str:
        return f"{provider_name}:{mpn.strip().lower()}"

    def get(self, provider_name: str, mpn: str) -> "MpnLookupResult | None":
        entry = self.get_entry(provider_name, mpn)
        return None if entry is None else entry[0]

    def get_entry(
        self, provider_name: str, mpn: str
    ) -> "tuple[MpnLookupResult, datetime] | None":
        """The cached result and when it was fetched from the provider."""
        key = self._make_key(provider_name, mpn)
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at, retrieved_at = item
        if _clock() >= expires_at:
            del self._store[key]
            return None
        # Move to end (most-recently-used).
        self._store.move_to_end(key)
        return value, retrieved_at  # type: ignore[return-value]

    def set(self, provider_name: str, mpn: str, value: "MpnLookupResult", ttl: float) -> None:
        key = self._make_key(provider_name, mpn)
        expires_at = _clock() + ttl
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, expires_at, utcnow())
        # Evict oldest entries when over capacity.
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)


# Module-level singleton shared across all requests (same process, --workers 1).
_cache = _TtlLruCache(maxsize=_MAX_ENTRIES, default_ttl=_HIT_TTL_SEC)


# ---------------------------------------------------------------------------
# Internal: per-provider circuit breakers
# ---------------------------------------------------------------------------

class _CircuitBreaker:
    """Consecutive-failure counter with a timed open state."""

    def __init__(self, threshold: int, open_sec: float) -> None:
        self._threshold = threshold
        self._open_sec = open_sec
        self._consecutive_failures: int = 0
        self._open_until: float = 0.0  # monotonic time
        self._suspended_until: float = 0.0  # monotonic time

    @property
    def is_open(self) -> bool:
        return _clock() < self._open_until

    def suspend(self, seconds: float) -> None:
        """Open for *seconds* because the provider suspended us."""
        self._suspended_until = _clock() + seconds
        self._open_until = max(self._open_until, self._suspended_until)

    def suspended_for(self) -> int:
        """Whole seconds of suspension left; 0 when not suspended."""
        left = self._suspended_until - _clock()
        return math.ceil(left) if left > 0 else 0

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            self._open_until = max(self._open_until, _clock() + self._open_sec)
            # Don't reset the counter — the next success after the breaker
            # closes will reset it; a failure immediately after reopening
            # re-opens instantly (already at threshold).


# One breaker per provider name.  Built lazily.
_breakers: dict[str, _CircuitBreaker] = {}


def _get_breaker(provider_name: str) -> _CircuitBreaker:
    if provider_name not in _breakers:
        _breakers[provider_name] = _CircuitBreaker(
            threshold=_CB_FAIL_THRESHOLD,
            open_sec=_CB_OPEN_SEC,
        )
    return _breakers[provider_name]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def suspended_for(provider_name: str) -> int:
    """Seconds left on *provider_name*'s suspension, 0 when there is none."""
    return _get_breaker(provider_name).suspended_for()


def peek_cached(
    provider_name: str, mpn: str
) -> "tuple[MpnLookupResult, datetime] | None":
    """A cached result and its fetch time, without touching the network.

    The agent path uses this to serve a hit WITHOUT pacing or a quota
    check, since a hit spends nothing (SM-001 acceptance 5).
    """
    return _cache.get_entry(provider_name, mpn.strip())


def _refuse_if_open(provider_name: str, breaker: _CircuitBreaker) -> None:
    left = breaker.suspended_for()
    if left:
        raise ProviderSuspendedError(
            provider_name,
            f"{provider_name} has suspended this application (HTTP 403); "
            f"retry in {left}s",
            retry_after=left,
        )
    if breaker.is_open:
        raise ProviderUpstreamError(
            provider_name,
            "provider temporarily unavailable (circuit breaker open)",
            status_code=503,
        )


def _live_call(
    provider: "PartsProvider", mpn: str, breaker: _CircuitBreaker
) -> "MpnLookupResult":
    """Call the provider and fold the answer into the breaker state."""
    try:
        result = provider.lookup_mpn(mpn)
    except ProviderUpstreamError:
        breaker.record_failure()
        raise

    if result.get("suspended"):
        breaker.suspend(_SUSPEND_SEC)
    elif result.get("found"):
        breaker.record_success()
    else:
        # Only count hard failures (network errors, 5xx, auth failures) as
        # breaker-trips — "no match for MPN" is a clean miss, not a failure.
        msg = (result.get("message") or "").lower()
        _is_hard_failure = any(
            token in msg
            for token in ("unavailable", "auth failed", "rate limit", "http 5", "http 4")
        )
        if _is_hard_failure:
            breaker.record_failure()
    return result


def lookup_with_cache(provider: "PartsProvider", mpn: str) -> "MpnLookupResult":
    """Call ``provider.lookup_mpn(mpn)`` with TTL caching and circuit-breaking.

    * If a cached positive result exists, return it without hitting the network.
    * If the circuit breaker is open, raise without calling upstream —
      `ProviderSuspendedError` while the provider has suspended us.
    * Otherwise, call the provider, update the cache, and update the breaker.
    """
    normalized_mpn = mpn.strip()
    breaker = _get_breaker(provider.name)

    # 1. Check the cache first (before the breaker so cached hits bypass both).
    cached = _cache.get(provider.name, normalized_mpn)
    if cached is not None:
        return cached

    # 2. Circuit breaker — don't call the upstream if it's been consistently
    #    failing.  This keeps the single uvicorn worker from stalling on a
    #    misbehaving upstream.
    _refuse_if_open(provider.name, breaker)

    # 3. Live call, breaker update.
    result = _live_call(provider, normalized_mpn, breaker)

    # 4. Cache the result (different TTLs for hits vs misses).
    if result.get("found"):
        _cache.set(provider.name, normalized_mpn, result, ttl=_HIT_TTL_SEC)
    else:
        # Cache clean misses ("no match for MPN") only — not hard failures,
        # so that a transient upstream error doesn't suppress future retries.
        msg = (result.get("message") or "").lower()
        if "no match" in msg or "empty mpn" in msg:
            _cache.set(provider.name, normalized_mpn, result, ttl=_MISS_TTL_SEC)

    return result


def lookup_fresh(provider: "PartsProvider", mpn: str) -> "MpnLookupResult":
    """Like ``lookup_with_cache`` but skips the cache read (always hits upstream).

    Use this for operator-initiated refresh actions where the intent is
    explicitly "fetch the latest from the upstream provider".  The circuit
    breaker is still applied so a broken upstream doesn't stall the worker.
    On a successful result, the cache is updated so subsequent
    ``lookup_with_cache`` calls see the fresh value.
    """
    normalized_mpn = mpn.strip()
    breaker = _get_breaker(provider.name)

    # Circuit breaker still applies — a manual refresh into a broken upstream
    # should fail fast, not pin the only uvicorn worker for 8 s.
    _refuse_if_open(provider.name, breaker)

    # Live call — deliberately no cache read here.
    result = _live_call(provider, normalized_mpn, breaker)

    # Overwrite the cache so the fresh result is visible to lookup_with_cache.
    if result.get("found"):
        _cache.set(provider.name, normalized_mpn, result, ttl=_HIT_TTL_SEC)

    return result
