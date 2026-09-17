"""The jobs a human runs by hand, and the flags only they read.

Split out of `run_job.py` for the 800-line ceiling, on the seam the
registry already draws: a SCHEDULED job is a one-line adapter over a
domain service and stays next to the registry, while an operator-run one
carries a report file, a `--apply` gate and — for two of them — flags no
other job understands. That is the half that keeps growing.

`run_job.py` imports this module at module level, so nothing here may
import back from it; the vocabulary they share lives in
`run_job_options.py`. Domain services are imported INSIDE each adapter,
not at the top, so the heartbeat probe still loads the CLI without
pulling in sqlalchemy (`tests/test_run_job_probe_imports.py`).
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.cli.run_job_options import (
    JobConfigError,
    JobHaltedError,
    JobOptions,
    report_stream,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "EXTRA_FLAGS",
    "add_operator_arguments",
    "run_category_seed_job",
    "run_part_rename_job",
    "run_provider_refresh_job",
    "run_spec_normalize_job",
    "run_symbol_collapse_job",
]


def run_category_seed_job(db: Session, options: JobOptions) -> int:
    from app.domain.categories.seed import run_category_seed

    with report_stream(options) as stream:
        return run_category_seed(
            db,
            apply=options.apply,
            workspace_id=options.workspace_id,
            stream=stream,
        )


def run_symbol_collapse_job(db: Session, options: JobOptions) -> int:
    from app.domain.eda.symbol_collapse import run_symbol_collapse

    with report_stream(options) as stream:
        return run_symbol_collapse(
            db,
            apply=options.apply,
            workspace_id=options.workspace_id,
            stream=stream,
        )


def run_spec_normalize_job(db: Session, options: JobOptions) -> int:
    from app.domain.parts.services.spec_normalize import normalize_specs

    # A `--workspace` that names nothing raises `UnknownWorkspaceError`,
    # a `LookupError` — the shape `main` already turns into exit 2 for the
    # other operator-run jobs. Nothing to translate here.
    with report_stream(options) as stream:
        outcome = normalize_specs(
            db,
            apply=options.apply,
            workspace_id=options.workspace_id,
            stream=stream,
        )
    return outcome.changes


def run_part_rename_job(db: Session, options: JobOptions) -> int:
    from app.domain.parts.services.part_rename import rename_parts

    # A `--workspace` that names nothing raises `UnknownWorkspaceError`,
    # the same `LookupError` the other operator-run jobs raise and `main`
    # already reports as a usage error.
    with report_stream(options) as stream:
        outcome = rename_parts(
            db,
            apply=options.apply,
            include_free=options.include_free,
            workspace_id=options.workspace_id,
            stream=stream,
        )
    return outcome.counts.renamed


def run_provider_refresh_job(db: Session, options: JobOptions) -> int:
    from app.domain.parts.services.provider_refresh_job import (
        DEFAULT_SLEEP_MS,
        ProviderQuotaExhausted,
        SweepAlreadyRunning,
        refresh_linked_parts,
        sweep_lock,
    )

    # A `--workspace` that names nothing raises `UnknownWorkspaceError`,
    # the `LookupError` shape `main` already reports as a usage error.
    try:
        # The lock is taken OUTSIDE `report_stream`, and the order is the
        # point: opening the report truncates it, so a run that is not
        # allowed to start would otherwise destroy the CSV belonging to
        # the sweep that IS running before finding out it may not run.
        with sweep_lock(db), report_stream(options) as stream:
            try:
                outcome = refresh_linked_parts(
                    db,
                    apply=options.apply,
                    workspace_id=options.workspace_id,
                    stream=stream,
                    limit=options.limit,
                    only_uncategorized=options.only_uncategorized,
                    link_missing_providers=options.link_missing_providers,
                    include_unlinked=options.include_unlinked,
                    sleep_ms=(
                        options.sleep_ms
                        if options.sleep_ms is not None
                        else DEFAULT_SLEEP_MS
                    ),
                    lock_held=True,
                )
            except ProviderQuotaExhausted as exc:
                # Translated at the boundary rather than raised from the
                # domain: `JobHaltedError` is the CLI's vocabulary for "a
                # job stopped on purpose and the exit code should say so",
                # and a domain service has no business importing it.
                raise JobHaltedError(str(exc)) from exc
    except SweepAlreadyRunning as exc:
        # Exit 2 with a message, not exit 0 with an empty report: for a
        # job whose purpose is to change a few hundred parts, "nothing to
        # do" is the most misleading answer available.
        raise JobConfigError(str(exc)) from exc
    return outcome.refreshed

#: `JobOptions` attributes a job opts into through `JobSpec.extra_flags`,
#: mapped to the parsed value that means "the operator did not pass it".
#:
#: A mapping rather than a list of names because "not given" is not the
#: same as "falsy" for every flag: `--sleep-ms 0` is an operator turning
#: the throttle off, and reading it as an absent flag would let it be
#: silently accepted by a job that does not understand it — the exact
#: failure mode this whole surface exists to avoid.
EXTRA_FLAGS: Mapping[str, object] = {
    "include_free": False,
    "limit": None,
    "only_uncategorized": False,
    "link_missing_providers": False,
    "include_unlinked": False,
    "sleep_ms": None,
}


def add_operator_arguments(parser: argparse.ArgumentParser) -> None:
    """Declare the flags only some operator-run jobs read.

    The parser is shared by every job, so argparse accepts all of
    these for all of them; `EXTRA_FLAGS` is what lets `run_job`
    refuse one by name for a job that does not declare it, which is
    the whole point of offering them here rather than per job.
    """
    parser.add_argument(
        "--include-free",
        action="store_true",
        help=(
            "part-rename only: also rename parts whose name is free text. "
            "Off by default — a hand-typed name is somebody's deliberate "
            "choice — and the report lists them either way."
        ),
    )
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=None,
        help=(
            "provider-refresh only: stop after this many parts, across the "
            "whole run. Every call it makes comes out of a metered daily "
            "allowance, so trying ten first is the normal way to start."
        ),
    )
    parser.add_argument(
        "--only-uncategorized",
        action="store_true",
        help=(
            "provider-refresh only: restrict the sweep to parts with no "
            "category, which are the ones with the most to gain."
        ),
    )
    parser.add_argument(
        "--link-missing-providers",
        action="store_true",
        help=(
            "provider-refresh only: also ask every provider this workspace "
            "has credentials for that the part is not linked to, and link it "
            "on an exact-MPN hit. Costs one extra call per part per provider."
        ),
    )
    parser.add_argument(
        "--include-unlinked",
        action="store_true",
        help=(
            "provider-refresh only: also visit active parts that no "
            "provider has ever been linked to, trying the primary first "
            "and then every secondary with credentials. On a hit the "
            "primary claims the part, filling only the columns it left "
            "empty. Costs one call per part per provider."
        ),
    )
    parser.add_argument(
        "--sleep-ms",
        metavar="MS",
        type=int,
        default=None,
        help=(
            "provider-refresh only: milliseconds between provider calls "
            "(default 750). 0 turns the throttle off."
        ),
    )
