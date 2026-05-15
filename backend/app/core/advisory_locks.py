"""Advisory-lock namespace registry."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

RUN_JOB_LOCK_CLASSID: Final[int] = 1
PASSWORD_RESET_THROTTLE_LOCK_CLASSID: Final[int] = 2

ADVISORY_LOCK_CLASSIDS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "run_job": RUN_JOB_LOCK_CLASSID,
        "password_reset_throttle": PASSWORD_RESET_THROTTLE_LOCK_CLASSID,
    }
)
