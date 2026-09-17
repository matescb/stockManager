"""What the runner and an operator-run job both have to name.

`run_job.py` owns the registry and the CLI; `run_job_operator.py` owns
the adapters for the jobs a human runs by hand. Both need `JobOptions`,
the report file it points at, and the three errors `main` turns into
exit codes — so those live here and the dependency arrows point one way,
`run_job` → `run_job_operator` → this module.

Nothing from sqlalchemy or `app.core` is imported here, for the same
reason `run_job.py` avoids them: the compose healthchecks run
`python -m app.cli.run_job --check-all-heartbeats` on a 0.5-CPU sidecar
and a cold `import sqlalchemy` there was measured in tens of seconds.
`tests/test_run_job_probe_imports.py` pins it.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from uuid import UUID

__all__ = [
    "JobConfigError",
    "JobHaltedError",
    "JobOptions",
    "UnknownJobError",
    "report_stream",
]


@dataclass(frozen=True)
class JobOptions:
    """The flags an operator-run job accepts.

    Only jobs that set `JobSpec.takes_options` receive one; the
    scheduled sidecar jobs take no arguments at all and their signature
    stays `run(db)`.

    `apply` is False by default and that is the whole point: these jobs
    change data nobody asked them to change on a timer, so the operator
    reads a report first. `run_job` rolls the transaction back after a
    dry run, so "the job forgot to check the flag" is not a way to write
    to production.

    `report` is where the job writes its CSV instead of stdout. Both
    jobs here honour it, and it lives on the shared options object
    rather than in either job because the two branches queued behind
    this one (`feat/spec-normalize-job` and `feat/part-naming-convention`)
    register operator jobs that want the same flag — a job opts in by
    passing `report_stream(options)` to whatever already takes a
    `stream`, and adds nothing to the parser.
    """

    apply: bool = False
    workspace_id: UUID | None = None
    report: Path | None = None
    #: `part-rename` only, declared through `JobSpec.extra_flags`. A job
    #: that does not declare it and is handed `--include-free` is
    #: refused by name, the same way a scheduled job handed `--apply` is.
    include_free: bool = False
    #: `provider-refresh` only, all five. They exist because that job
    #: spends a metered external resource: `limit` caps the parts a run
    #: touches, `only_uncategorized` narrows it to the parts with the
    #: most to gain, `link_missing_providers` widens what each part is
    #: asked, `include_unlinked` widens WHICH parts are asked about, and
    #: `sleep_ms` paces the calls. `None` means "not given" for
    #: the two that take a value — `sleep_ms=0` is a real choice (turn the
    #: throttle off) and must not read as an absent flag.
    limit: int | None = None
    only_uncategorized: bool = False
    link_missing_providers: bool = False
    include_unlinked: bool = False
    sleep_ms: int | None = None


class UnknownJobError(ValueError):
    """Raised when the requested job name is not registered."""


class JobConfigError(ValueError):
    """Raised when a job's settings-backed configuration is invalid."""


class JobHaltedError(RuntimeError):
    """A job stopped early ON PURPOSE and wants a distinct exit code.

    Not a failure: the job has already written its report and, on
    `--apply`, committed what it finished. `main` reports it as exit 3,
    which is neither 0 ("done") nor 2 ("you asked for something
    impossible") — a monitor or a runbook step can tell "stopped, re-run
    later" from both. `provider-refresh` raises it when a provider says
    the day's quota is gone.
    """


@contextmanager
def report_stream(options: JobOptions) -> Iterator[TextIO | None]:
    """The file `--report` named, or None for "write to stdout".

    `newline=""` because the payload is CSV: `csv` writes its own line
    terminator, and letting the text layer translate it again produces
    CRLFCRLF on a platform that does.

    The file is written even on a dry run, and that is the point — the
    report IS the deliverable of a dry run. The transaction rolls back;
    the operator still has the CSV to read.

    It is written 0600, in a 0700 directory when the job has to create
    one. These reports name every workspace, part, key and value they
    touch, and they land wherever the operator pointed — which on the
    prod container is a world-readable `/tmp`. `exist_ok=True` does not
    re-mode a directory that already exists, so `--report /tmp/x.csv`
    hardens the file and never touches `/tmp` itself.
    """
    if options.report is None:
        yield None
        return
    try:
        options.report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = options.report.open("w", encoding="utf-8", newline="")
    except OSError as exc:
        # Same contract as a `--workspace` that names nothing: a usage
        # error the operator can read and fix, not a traceback. Only the
        # open is wrapped — an OSError raised later, from inside the job,
        # is a real failure and must keep its stack.
        raise JobConfigError(f"cannot write --report {options.report}: {exc}") from exc
    with handle:
        # After the open, so the mode applies to the file that exists
        # rather than racing whatever umask the operator's shell carries.
        os.chmod(options.report, 0o600)
        yield handle
