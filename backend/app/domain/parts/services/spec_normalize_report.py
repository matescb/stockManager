"""The CSV an operator reviews between `spec-normalize --dry-run` and
`--apply`.

The job rewrites `custom_fields` in bulk on a system with no staging
environment, so the review artifact is part of the feature, not a
nicety. Three things follow from that:

* **it is written by the dry run and the apply alike**, from the same
  code path, so what the operator approved is what runs;
* **it streams.** One line per change, flushed per batch — a run killed
  halfway still leaves a readable file naming everything it did;
* **it carries its own summary.** Counts per action per workspace, and
  the raw keys the schema had no alias for, which is the list the alias
  table in `spec_schema_tables.py` gets extended from.

The change section is plain CSV with the columns in `REPORT_COLUMNS`.
The two summary sections follow it, each after a blank line and each
with its own header row, so a spreadsheet import reads the changes and a
`grep` reads the totals.
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, TextIO
from uuid import UUID

__all__ = [
    "ACTION_ADD",
    "ACTION_ARCHIVE",
    "ACTION_CATEGORY",
    "ACTION_DROP",
    "ACTION_REKEY",
    "ACTION_STAMP",
    "ACTION_VALUE_NUM",
    "ACTIONS",
    "REPORT_COLUMNS",
    "Change",
    "NormalizeReport",
    "UNMAPPED_REPORT_LIMIT",
    "rank_unmapped",
]

#: The row now carries its canonical key and the parsed display value.
#: `old_key == key` means the key was already canonical and only the
#: value moved.
ACTION_REKEY = "rekey"
#: Retired with `archived_at`: a customs code, or an alias superseded by
#: the spelling that won its canonical key.
ACTION_ARCHIVE = "archive"
#: Retired with `archived_at` because the value was a placeholder (`-`).
#: Physically the same as `archive`; the two are separate actions because
#: the reason is what the operator is reviewing.
ACTION_DROP = "drop"
#: A canonical row that already had the right key and value but no
#: `custom_fields.provider`.
ACTION_STAMP = "stamp"
#: A canonical row whose numeric sidecar was missing.
ACTION_VALUE_NUM = "value_num"
#: A part with no category, filed from its provider's taxonomy.
ACTION_CATEGORY = "category"
#: A NEW `custom_fields` row, inserted rather than renamed. The one case
#: is the schema's one-to-many alias: `Size / Dimension` answers both
#: `length` and `width`, and the part has a single row for it. The first
#: canonical key renames that row; the second cannot, so it gets a copy.
#: `old_key` names the upstream key the value was read from, which is how
#: an operator ties the insert back to the rename on the line above it.
#: Reversal is a DELETE of this row, not a rename — see the runbook.
ACTION_ADD = "add"

ACTIONS: tuple[str, ...] = (
    ACTION_REKEY,
    ACTION_ARCHIVE,
    ACTION_DROP,
    ACTION_STAMP,
    ACTION_VALUE_NUM,
    ACTION_CATEGORY,
    ACTION_ADD,
)

REPORT_COLUMNS: tuple[str, ...] = (
    "workspace_id",
    "part_id",
    "mpn",
    "action",
    "key",
    "old_key",
    "provider",
    "old_value",
    "new_value",
    "category_path",
)

_COUNT_SECTION_HEADER = ("count", "workspace_id", "action", "count")
_UNMAPPED_SECTION_HEADER = ("unmapped", "category_slug", "key", "count")


@dataclass(frozen=True)
class Change:
    """One thing the job did (or, in a dry run, would do) to one part.

    The workspace, part and MPN are supplied by the writer, which knows
    the context the change was produced in — a `Change` is only what
    changed.
    """

    action: str
    key: str = ""
    old_key: str = ""
    provider: str = ""
    old_value: str = ""
    new_value: str = ""
    category_path: str = ""


class NormalizeReport:
    """Streaming CSV writer over a stream the CALLER owns.

    `stream` is what `run_job._report_stream` yields: the file `--report`
    named, or ``None`` for stdout. Opening and closing belong to that
    context manager, not here — every operator-run job gets its report
    file on the same terms, including the `newline=""` the `csv` module
    needs and the readable error when the path cannot be written.
    """

    def __init__(self, stream: TextIO | None) -> None:
        self._handle: TextIO = stream if stream is not None else sys.stdout
        # `csv.writer` returns a private `_csv.writer` type with no public
        # name to annotate against.
        self._writer: Any = csv.writer(self._handle)
        self._writer.writerow(REPORT_COLUMNS)

    def write(
        self,
        *,
        workspace_id: UUID,
        part_id: UUID,
        mpn: str | None,
        change: Change,
    ) -> None:
        self._writer.writerow(
            (
                str(workspace_id),
                str(part_id),
                mpn or "",
                change.action,
                change.key,
                change.old_key,
                change.provider,
                change.old_value or "",
                change.new_value or "",
                change.category_path,
            )
        )

    def flush(self) -> None:
        """Called at each batch boundary: a killed run keeps its file."""
        self._handle.flush()

    def write_summary(
        self,
        *,
        per_workspace: Mapping[UUID, Mapping[str, int]],
        unmapped: Iterable[tuple[str, str, int]],
    ) -> None:
        self._writer.writerow(())
        self._writer.writerow(_COUNT_SECTION_HEADER)
        for workspace_id in sorted(per_workspace, key=str):
            counts = per_workspace[workspace_id]
            for action in ACTIONS:
                if counts.get(action):
                    self._writer.writerow(
                        ("count", str(workspace_id), action, counts[action])
                    )
        self._writer.writerow(())
        self._writer.writerow(_UNMAPPED_SECTION_HEADER)
        for category_slug, key, count in unmapped:
            self._writer.writerow(("unmapped", category_slug, key, count))
        self._handle.flush()


#: How many unmapped raw keys a report lists. The list exists to be read
#: and turned into alias-table entries, so it is a shortlist. Shared with
#: `provider-refresh`, whose summary section means the same thing and had
#: better be the same length.
UNMAPPED_REPORT_LIMIT = 30


def rank_unmapped(
    unmapped: Mapping[tuple[str, str], int],
    *,
    limit: int = UNMAPPED_REPORT_LIMIT,
) -> tuple[tuple[str, str, int], ...]:
    """The `(category_slug, raw_key)` pairs the schema had no home for,
    most frequent first.

    Tied counts break on `(slug, key)` so two runs over the same data
    produce the same file — a report that is diffable against the
    previous one is worth more than one that is merely correct.
    """
    ranked = sorted(unmapped.items(), key=lambda item: (-item[1], item[0]))
    return tuple((slug, key, count) for (slug, key), count in ranked[:limit])
