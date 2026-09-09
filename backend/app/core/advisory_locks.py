"""Advisory-lock namespace registry."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

RUN_JOB_LOCK_CLASSID: Final[int] = 1
PASSWORD_RESET_THROTTLE_LOCK_CLASSID: Final[int] = 2
# SESSION-level (not xact-level) namespace. `run_job` takes its lock with
# pg_try_advisory_xact_lock, which Postgres releases at the first COMMIT — so
# a job that commits mid-run, as the datasheet backfill deliberately does to
# keep its attempt counters, loses that protection partway through. This
# namespace is for a lock the job holds across its own commits.
DATASHEET_BACKFILL_LOCK_CLASSID: Final[int] = 3

ADVISORY_LOCK_CLASSIDS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "run_job": RUN_JOB_LOCK_CLASSID,
        "password_reset_throttle": PASSWORD_RESET_THROTTLE_LOCK_CLASSID,
        "datasheet_backfill": DATASHEET_BACKFILL_LOCK_CLASSID,
    }
)
