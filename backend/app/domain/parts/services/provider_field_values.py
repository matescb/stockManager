"""Cap a provider-supplied value at the `custom_fields.value` width.

Its own module because three writers need it and a shared import must
not drag one of them into the others' import graph:
`services/provider_import.py` (create), `services/spec_reconcile.py`
(both paths) and `api/routes/parts_refresh.py` (refresh). Putting it in
`provider_import.py`, where it started, made `spec_reconcile` import the
module that imports `spec_reconcile`.
"""
from __future__ import annotations

__all__ = ["CUSTOM_FIELD_VALUE_MAX", "truncate_provider_field_value"]

CUSTOM_FIELD_VALUE_MAX = 1024
_TRUNCATION_SENTINEL = "\n[truncated by provider import]"


def truncate_provider_field_value(value: str) -> str:
    """Keep the head and say so, rather than let the DB raise a DataError.

    The sentinel is inside the cap, so the result always fits the column
    even when the caller hands us something at exactly the limit.
    """
    if len(value) <= CUSTOM_FIELD_VALUE_MAX:
        return value
    keep = CUSTOM_FIELD_VALUE_MAX - len(_TRUNCATION_SENTINEL)
    return value[:keep] + _TRUNCATION_SENTINEL
