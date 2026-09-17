"""How a `provider-refresh` sweep reads a failure.

Two questions, both answered from a message or an exception and nothing
else — no session, no workspace, no outbound request. Split out of
`provider_refresh_job.py` for the 800-line ceiling, the third time that
file has been split and on the same kind of seam as the other two
(`provider_refresh_scope.py` answers "which parts", and
`provider_refresh_report.py` "what does the CSV look like").

* **Is this the day's quota gone?** If so the sweep stops rather than
  burning the rest of the run against a provider that is refusing.
  Getting it wrong is expensive in both directions: a false positive
  ends a run that had hundreds of good parts left, and a false negative
  marks every remaining part "the provider has never heard of this" and
  writes a catalogue-wide lie into the report.
* **What do I call a write the database rejected?** A bounded reason for
  one CSV cell.

See `docs/runbooks/provider-refresh.md` and ADR-0021.
"""
from __future__ import annotations

import re

from sqlalchemy.exc import IntegrityError

__all__ = ["looks_like_quota", "rejected_write"]

#: What a provider says when the daily allowance is gone. Matched against
#: the message of a `found: False` answer AND of a raised `ProviderError`,
#: because the two providers disagree about which one a 429 is: DigiKey
#: returns `{"found": False, "message": "DigiKey rate limit reached"}`
#: while a transport-level 429 arrives as an exception.
_QUOTA_TOKENS: tuple[str, ...] = (
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many request",
    "quota",
    "calls exceeded",
    "limit exceeded",
)

#: The other half, and it cannot be a bare `"429"` substring: provider
#: messages quote MPNs, and `no match for MPN SN74HC4290` would then halt
#: the sweep and blame a provider that is answering fine. It also cannot
#: be dropped in favour of `status_code` alone — Mouser's transport layer
#: turns a 429 into a `ProviderUpstreamError` whose `status_code` is 502
#: and whose MESSAGE is `Mouser upstream returned HTTP 429`, so the
#: number only survives in the text.
_HTTP_429 = re.compile(r"\bhttp\b\D{0,3}429\b", re.IGNORECASE)


def looks_like_quota(message: str) -> bool:
    """Does this provider message mean "the day's allowance is gone"?"""
    lowered = (message or "").lower()
    if any(token in lowered for token in _QUOTA_TOKENS):
        return True
    return _HTTP_429.search(lowered) is not None


def rejected_write(exc: IntegrityError) -> str:
    """A bounded reason for the CSV's `error` cell.

    `str(IntegrityError)` carries the whole failing statement and its
    bound parameters: a cell nobody can read across a 300-row file, and
    on this table a second copy of values the report already names in
    columns of their own. The constraint name is the part an operator
    acts on — `uq_parts_ws_mpn` says "another part in this workspace
    already has that MPN".
    """
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    name = getattr(diagnostic, "constraint_name", None)
    if name:
        return f"database constraint {name} rejected the write"
    return "a database constraint rejected the write"
