"""The heartbeat probe must stay cheap: importing the CLI pulls in no ORM.

`docker-compose.prod.yml` runs `python -m app.cli.run_job --check-all-heartbeats`
as the healthcheck of two 0.5-CPU sidecars with a short timeout. The probe
only stats heartbeat files, so a cold `import sqlalchemy` (measured at tens of
seconds under host load) must not ride along with the module import.
"""

from __future__ import annotations

import subprocess
import sys

_HEAVY = ("sqlalchemy", "app.infra.db", "app.domain.all_models")


def test_importing_the_cli_does_not_import_the_orm() -> None:
    code = (
        "import sys; import app.cli.run_job; "
        f"print(sorted(m for m in {_HEAVY!r} if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", out.stdout


def test_the_lock_still_reaches_the_advisory_lock_class_id() -> None:
    # The lazy import inside `_acquire_job_lock` must resolve to the same
    # constant the rest of the app uses; a typo there would only show up in
    # prod when two sidecars raced.
    from app.cli import run_job
    from app.core.advisory_locks import RUN_JOB_LOCK_CLASSID

    src = run_job._acquire_job_lock.__code__.co_names
    assert "RUN_JOB_LOCK_CLASSID" in src
    assert isinstance(RUN_JOB_LOCK_CLASSID, int)
