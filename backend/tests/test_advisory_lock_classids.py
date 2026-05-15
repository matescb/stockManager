from __future__ import annotations

import re
from pathlib import Path

from app.core.advisory_locks import (
    ADVISORY_LOCK_CLASSIDS,
    PASSWORD_RESET_THROTTLE_LOCK_CLASSID,
    RUN_JOB_LOCK_CLASSID,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
INT4_MIN = -(2**31)
INT4_MAX = 2**31 - 1


def test_classids_disjoint() -> None:
    classids = dict(ADVISORY_LOCK_CLASSIDS)

    assert classids == {
        "run_job": RUN_JOB_LOCK_CLASSID,
        "password_reset_throttle": PASSWORD_RESET_THROTTLE_LOCK_CLASSID,
    }
    assert len(set(classids.values())) == len(classids)
    assert all(INT4_MIN <= classid <= INT4_MAX for classid in classids.values())


def test_classids_documented() -> None:
    docs = (REPO_ROOT / "docs" / "development.md").read_text(encoding="utf-8")

    for feature, classid in ADVISORY_LOCK_CLASSIDS.items():
        assert re.search(
            rf"\|\s*`{classid}`\s*\|\s*`{re.escape(feature)}`\s*\|",
            docs,
        )


def test_hashtext_lock_sites_use_two_arg_namespaces() -> None:
    run_job_source = (
        REPO_ROOT / "backend" / "app" / "cli" / "run_job.py"
    ).read_text(encoding="utf-8")
    auth_source = (
        REPO_ROOT / "backend" / "app" / "api" / "routes" / "auth.py"
    ).read_text(encoding="utf-8")

    assert "pg_try_advisory_xact_lock(hashtext(" not in run_job_source
    assert "pg_advisory_xact_lock(hashtext(" not in auth_source
    assert "RUN_JOB_LOCK_CLASSID" in run_job_source
    assert "PASSWORD_RESET_THROTTLE_LOCK_CLASSID" in auth_source
    assert "CAST(:classid AS int4)" in run_job_source
    assert "CAST(:classid AS int4)" in auth_source
    assert "CAST(hashtext(:job_name) AS int4)" in run_job_source
    assert "CAST(hashtext(:lock_key) AS int4)" in auth_source
