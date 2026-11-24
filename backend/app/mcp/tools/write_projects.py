"""Mutating MCP tools for project BOMs.

`sync_project_bom` is the twin of `POST /api/projects/{id}/bom/sync`
(SM-011). Both call `domain/projects/bom_sync.py::sync`, so the diff,
the line-identity rule and the audit rows are the same whichever door
the BOM came through.
"""
from __future__ import annotations

from typing import Any

from fastapi import status
from pydantic import ValidationError

from app.core.errors import ErrorCodes, raise_http
from app.domain.projects import bom_sync
from app.domain.projects.schemas import BomSyncIn
from app.mcp.principal import Caller
from app.mcp.tools._registry import tool
from app.mcp.tools.read import _project

# Matches the REST route's `@limiter.limit("30/minute")`.
_SYNC_RATE = "30/minute"


def _payload(rows: list[dict[str, Any]], dry_run: bool) -> BomSyncIn:
    """`BomSyncIn` from the tool's arguments, or a refusal naming the field."""
    try:
        return BomSyncIn(rows=rows, dry_run=dry_run)
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(str(part) for part in error["loc"]) or "?" for error in exc.errors()[:10]
        )
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.BOM_SYNC_INVALID_ROW,
            f"invalid value for {fields}: {exc.error_count()} field(s) failed validation",
        )
        raise AssertionError("unreachable")  # pragma: no cover


@tool(writes=True, rate=_SYNC_RATE)
def sync_project_bom(
    caller: Caller,
    project_id: str,
    rows: list[dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update a project's BOM in place from an EDA export, keeping history.

    Args:
        project_id: The project's id, from `list_projects`.
        rows: One object per BOM line: `designators` (list, or a string
            like "R1,R2"), `quantity` (defaults to the designator count),
            `mpn`, `manufacturer`, `value`, `name`, `footprint`,
            `id_code` (the part's `StockManager` URL from KiCad, or its
            bare id), `ipn`, `cad_key`, `dnp`, `comments`. A field left
            out leaves the existing line's value alone.
        dry_run: true returns the diff and writes nothing. Do this first.

    A line is identified by its designator set (case and order do not
    matter); a line without designators by its matched part, then its
    cad_key, then its name/value/mpn. Lines whose designators are not in
    `rows` are archived as `removed`, never deleted, and come back with
    their history if they reappear. Unchanged lines are not written.

    Returns counts `added`, `updated`, `removed`, `unchanged`,
    `unmatched` (lines with no part link — create or match the part,
    then sync again) and `lines`, the same five as lists of
    `{entry_id, designators, part_id, name, changed?, restored?}`.
    """
    project = _project(caller, project_id)
    payload = _payload(rows, dry_run)
    return bom_sync.sync(
        caller.db,
        ws=caller.ws,
        user=caller.user,
        project=project,
        rows=payload.rows,
        dry_run=payload.dry_run,
        request_id=caller.principal.request_id,
    )
