"""The CSV an operator reviews between `provider-refresh --dry-run` and
`--apply`.

Same contract as `spec_normalize_report.py`, for the same reason: the
job rewrites part columns and spec rows in bulk on a system with no
staging environment, so the review artifact is part of the feature.

* **written by the dry run and the apply alike**, from one code path, so
  what the operator approved is what runs;
* **streamed**, one line per (part, provider), flushed per batch — a run
  the quota cut short still leaves a readable file naming everything it
  did and where it stopped;
* **carries its own summary**: counts per action per provider per
  workspace, and the raw keys the schema had no alias for.

One line per (part, provider) pair rather than per part: a prod part can
be linked to two providers, the tiers do different things, and collapsing
them would hide which one moved a column.
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, TextIO
from uuid import UUID

__all__ = [
    "ACTION_ERROR",
    "ACTION_LINKED",
    "ACTION_MISS",
    "ACTION_REFRESHED",
    "ACTION_SKIPPED",
    "ACTIONS",
    "REPORT_COLUMNS",
    "TIER_PRIMARY",
    "TIER_SECONDARY",
    "RefreshRow",
    "RefreshReport",
]

#: The provider answered and its payload was reconciled onto a part it
#: was already linked to.
ACTION_REFRESHED = "refreshed"
#: The same, on a provider that had no claim on the part at all — only
#: `--link-missing-providers` produces these, and only on an exact-MPN
#: hit. The new association is the change to review. A part whose
#: `linked_provider` column already named the provider reads as
#: `refreshed` even when the sweep backfills its missing link row: the
#: association was already a fact.
ACTION_LINKED = "linked"
#: The provider has never heard of this MPN, or answered with a different
#: one. Nothing written, no link created; not a failure.
ACTION_MISS = "miss"
#: The lookup raised. One part's problem — the sweep carries on, and the
#: `error` column says what happened.
ACTION_ERROR = "error"
#: The part is linked to a provider this workspace has no usable
#: credentials for, so there was nothing to ask.
ACTION_SKIPPED = "skipped"

ACTIONS: tuple[str, ...] = (
    ACTION_REFRESHED,
    ACTION_LINKED,
    ACTION_MISS,
    ACTION_ERROR,
    ACTION_SKIPPED,
)

TIER_PRIMARY = "primary"
TIER_SECONDARY = "secondary"

REPORT_COLUMNS: tuple[str, ...] = (
    "workspace_id",
    "part_id",
    "mpn",
    "provider",
    "tier",
    "action",
    # Space-separated column names, not values: the values are on the
    # part and in the audit trail, and a CSV cell holding a description
    # is a cell nobody can read across a 300-row file.
    "part_columns_changed",
    "specs_added",
    "specs_updated",
    # Rows brought back out of `archived_at` because upstream answered
    # their key again. Counted apart from `updated`: the value may not
    # have moved at all, but the row reappearing IS the change.
    "specs_restored",
    # Both ways a row leaves the Specs tab: hard-deleted because the
    # provider stopped sending the key, and retired with `archived_at`
    # because it is a customs code or a `-` placeholder. One column,
    # because the operator's question here is "how much did this part
    # lose", and the reconcile's own audit row carries the split.
    "specs_removed",
    "category_before",
    "category_after",
    # Exactly one of the two is ever populated on a row: an apply
    # downloads and fills `assets_fetched`; a dry run downloads nothing —
    # a file in UPLOAD_DIR is the one thing a rolled-back savepoint
    # cannot take back — and names what it would have pulled here.
    "assets_fetched",
    "assets_would_fetch",
    "error",
)

_COUNT_SECTION_HEADER = ("count", "workspace_id", "provider", "action", "count")
_UNMAPPED_SECTION_HEADER = ("unmapped", "category_slug", "key", "count")


@dataclass(frozen=True)
class RefreshRow:
    """One (part, provider) pair's line."""

    workspace_id: UUID
    part_id: UUID
    mpn: str
    provider: str
    tier: str
    action: str
    part_columns_changed: tuple[str, ...] = ()
    specs_added: int = 0
    specs_updated: int = 0
    specs_restored: int = 0
    specs_removed: int = 0
    category_before: str = ""
    category_after: str = ""
    assets_fetched: tuple[str, ...] = ()
    assets_would_fetch: tuple[str, ...] = ()
    error: str = ""


class RefreshReport:
    """Streaming CSV writer over a stream the CALLER owns.

    `stream` is what `run_job._report_stream` yields: the file `--report`
    named, or ``None`` for stdout. Opening, closing, the 0600 mode and
    the readable error when the path cannot be written all belong to
    that context manager — every operator-run job reports on the same
    terms.
    """

    def __init__(self, stream: TextIO | None) -> None:
        self._handle: TextIO = stream if stream is not None else sys.stdout
        # `csv.writer` returns a private `_csv.writer` type with no public
        # name to annotate against.
        self._writer: Any = csv.writer(self._handle)
        self._writer.writerow(REPORT_COLUMNS)

    def write(self, row: RefreshRow) -> None:
        self._writer.writerow(
            (
                str(row.workspace_id),
                str(row.part_id),
                row.mpn,
                row.provider,
                row.tier,
                row.action,
                " ".join(row.part_columns_changed),
                row.specs_added,
                row.specs_updated,
                row.specs_restored,
                row.specs_removed,
                row.category_before,
                row.category_after,
                " ".join(row.assets_fetched),
                " ".join(row.assets_would_fetch),
                row.error,
            )
        )

    def flush(self) -> None:
        """Called at each batch boundary: a killed run keeps its file."""
        self._handle.flush()

    def write_summary(
        self,
        *,
        per_provider: Mapping[tuple[UUID, str], Mapping[str, int]],
        unmapped: Iterable[tuple[str, str, int]],
    ) -> None:
        """The two sections after the changes, each behind a blank line.

        A spreadsheet import reads the change section and a `grep` reads
        the totals — the same shape `spec-normalize` writes, so an
        operator who has read one report can read this one.
        """
        self._writer.writerow(())
        self._writer.writerow(_COUNT_SECTION_HEADER)
        for workspace_id, provider in sorted(per_provider, key=lambda key: (str(key[0]), key[1])):
            counts = per_provider[(workspace_id, provider)]
            for action in ACTIONS:
                if counts.get(action):
                    self._writer.writerow(
                        ("count", str(workspace_id), provider, action, counts[action])
                    )
        self._writer.writerow(())
        self._writer.writerow(_UNMAPPED_SECTION_HEADER)
        for category_slug, key, count in unmapped:
            self._writer.writerow(("unmapped", category_slug, key, count))
        self._handle.flush()
