"""BOM CSV/TSV importer per spec §10.6 + §16.3."""
from __future__ import annotations

import base64
import csv
import io
from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

import chardet
from fastapi import status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes, raise_http
from app.domain.parts.models import Part, PartCadKey
from app.domain.projects.models import Project, ProjectEntry
from app.domain.projects.schemas import (
    BomImportCommitIn,
    BomImportCommitOut,
    BomImportPreviewIn,
    BomImportPreviewOut,
    BomMappingField,
    BomPreviewRow,
)

# SEC2-007 / BE2-006 — second line of defence. The schema caps the
# base64 input at 5 MB; this caps the decoded body at 4 MB and the row
# count at 10 000 so a malicious payload that squeaks past the schema
# (e.g. a wildly compressible CSV that base64-decodes oddly) still
# can't OOM the worker.
_MAX_DECODED_BYTES = 4_000_000
_MAX_ROW_COUNT = 10_000


def _decode_b64(b64: str) -> bytes:
    raw = base64.b64decode(b64)
    if len(raw) > _MAX_DECODED_BYTES:
        raise_http(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            ErrorCodes.BOM_TOO_LARGE,
            f"BOM payload exceeds {_MAX_DECODED_BYTES} bytes after decode",
            max_bytes=_MAX_DECODED_BYTES,
            actual_bytes=len(raw),
        )
    return raw


def _enforce_row_cap(rows: list) -> None:
    if len(rows) > _MAX_ROW_COUNT:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.BOM_TOO_MANY_ROWS,
            f"BOM exceeds {_MAX_ROW_COUNT} rows; split the import",
            max_rows=_MAX_ROW_COUNT,
            actual_rows=len(rows),
        )


def _detect_encoding(raw: bytes, hint: str | None) -> str:
    if hint:
        return hint
    guess = chardet.detect(raw[: 64 * 1024]) if raw else None
    enc = (guess or {}).get("encoding") or "utf-8"
    # Normalise common labels.
    if enc.lower() in {"ascii", "us-ascii"}:
        enc = "utf-8"
    return enc


def _detect_separator(text: str, hint: str | None) -> str:
    if hint:
        return hint
    sample = "\n".join(text.splitlines()[:20])
    candidates = [",", ";", "\t", "|"]
    counts = {c: sample.count(c) for c in candidates}
    sep = max(counts, key=lambda k: counts[k])
    return sep if counts[sep] > 0 else ","


def _looks_like_header(row: list[str]) -> bool:
    if not row:
        return False
    return all(not _looks_numeric(cell) for cell in row[:3])


def _looks_numeric(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return False
    try:
        float(s.replace(",", "."))
        return True
    except ValueError:
        return False


def preview(payload: BomImportPreviewIn) -> BomImportPreviewOut:
    raw = _decode_b64(payload.text_b64)
    enc = _detect_encoding(raw, payload.encoding)
    text = raw.decode(enc, errors="replace")
    sep = _detect_separator(text, payload.separator)
    rows_iter = csv.reader(io.StringIO(text), delimiter=sep)
    all_rows: list[list[str]] = [list(r) for r in rows_iter if any(c.strip() for c in r)]
    _enforce_row_cap(all_rows)
    if not all_rows:
        return BomImportPreviewOut(
            detected_separator=sep,
            detected_encoding=enc,
            has_header=False,
            headers=None,
            rows=[],
        )
    has_header = payload.has_header if payload.has_header is not None else _looks_like_header(all_rows[0])
    headers = all_rows[0] if has_header else None
    body = all_rows[1:] if has_header else all_rows
    return BomImportPreviewOut(
        detected_separator=sep,
        detected_encoding=enc,
        has_header=has_header,
        headers=headers,
        rows=[BomPreviewRow(cells=r) for r in body[:200]],
    )


@dataclass
class ParsedRow:
    # DB-005 / migration 0032 — quantity is integer; fractional values are a
    # per-row validation error recorded in row_errors at commit time.
    quantity: int = 1
    quantity_raw: str = ""  # original string value, kept for error reporting
    part: str | None = None
    mpn: str | None = None
    manufacturer: str | None = None
    internal_part_number: str | None = None
    designators: list[str] | None = None
    comments: str | None = None
    footprint: str | None = None
    id_code: str | None = None
    cad_key: str | None = None
    dnp: bool = False


def _parse_quantity(s: str) -> tuple[int, bool]:
    """Return (quantity, is_valid).

    Returns (1, True) for blank/missing, (n, True) for a non-negative integer
    string, and (0, False) for a fractional or otherwise invalid value.
    DB-005 / migration 0032.
    """
    raw = (s or "").strip().replace(",", ".")
    if not raw:
        return 1, True
    try:
        f = float(raw)
    except ValueError:
        return 1, True  # unparseable → default 1, no fraction error
    if f != int(f):
        return 0, False  # fractional — caller will record a row error
    return max(0, int(f)), True


def _parse_dnp(s: str) -> bool:
    return (s or "").strip().lower() in {"1", "true", "yes", "y", "dnp", "do not place"}


def _split_designators(value: str, sep: str) -> list[str]:
    if not value:
        return []
    parts = [p.strip() for p in value.split(sep)]
    return [p for p in parts if p]


def _apply_mapping(cells: list[str], mapping: list[BomMappingField], designator_sep: str) -> ParsedRow:
    out = ParsedRow()
    for m in mapping:
        idx = m.column_index
        if idx >= len(cells):
            continue
        v = cells[idx]
        t = m.target
        if t == "ignore":
            continue
        if t == "quantity":
            qty, valid = _parse_quantity(v)
            out.quantity = qty
            if not valid:
                out.quantity_raw = v
        elif t == "part":
            out.part = v.strip() or None
        elif t == "mpn":
            out.mpn = v.strip() or None
        elif t == "manufacturer":
            out.manufacturer = v.strip() or None
        elif t == "internal_part_number":
            out.internal_part_number = v.strip() or None
        elif t == "designators":
            out.designators = _split_designators(v, designator_sep)
        elif t == "comments":
            out.comments = v
        elif t == "footprint":
            out.footprint = v.strip() or None
        elif t == "id_code":
            out.id_code = v.strip() or None
        elif t == "cad_key":
            out.cad_key = v.strip() or None
        elif t == "dnp":
            out.dnp = _parse_dnp(v)
    return out


def _match_part(db: Session, *, workspace_id: UUID, row: ParsedRow) -> Part | None:
    """Spec §16.3 match priority — never auto-create."""
    # 1. internal ID code → we treat the part `id` UUID as the ID code if present.
    if row.id_code:
        try:
            uuid_val = UUID(row.id_code)
        except ValueError:
            uuid_val = None
        if uuid_val:
            p = db.get(Part, uuid_val)
            if p and p.workspace_id == workspace_id:
                return p
    # 2. CAD key
    if row.cad_key:
        cad = db.execute(
            select(Part)
            .join(PartCadKey, PartCadKey.part_id == Part.id)
            .where(Part.workspace_id == workspace_id)
            .where(PartCadKey.cad_key == row.cad_key)
            .limit(1)
        ).scalars().first()
        if cad:
            return cad
    # 3. exact internal_part_number
    if row.internal_part_number:
        p = db.execute(
            select(Part)
            .where(Part.workspace_id == workspace_id)
            .where(Part.internal_part_number == row.internal_part_number)
            .limit(1)
        ).scalars().first()
        if p:
            return p
    # 4. exact MPN (+ manufacturer if present)
    if row.mpn:
        q = select(Part).where(Part.workspace_id == workspace_id).where(Part.mpn == row.mpn)
        if row.manufacturer:
            q = q.where(func.lower(Part.manufacturer) == row.manufacturer.lower())
        p = db.execute(q.limit(1)).scalars().first()
        if p:
            return p
    # 5. exact local name
    if row.part:
        p = db.execute(
            select(Part).where(Part.workspace_id == workspace_id).where(Part.name == row.part).limit(1)
        ).scalars().first()
        if p:
            return p
    # 6. meta-part candidate — out of MVP
    return None


def commit(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    project: Project,
    payload: BomImportCommitIn,
) -> BomImportCommitOut:
    raw = _decode_b64(payload.text_b64)
    text = raw.decode(payload.encoding or "utf-8", errors="replace")
    rows_iter = csv.reader(io.StringIO(text), delimiter=payload.separator or ",")
    all_rows: list[list[str]] = [list(r) for r in rows_iter if any(c.strip() for c in r)]
    _enforce_row_cap(all_rows)
    if payload.has_header and all_rows:
        all_rows = all_rows[1:]

    # Find next order_index
    next_idx = db.execute(
        select(func.coalesce(func.max(ProjectEntry.order_index), -1))
        .where(ProjectEntry.workspace_id == workspace_id)
        .where(ProjectEntry.project_id == project.id)
    ).scalar_one() + 1

    # DB-005 / migration 0032 — pre-scan for fractional quantities so we can
    # return a descriptive 422 before touching the DB.
    fractional_rows: list[int] = []
    parsed_rows: list[ParsedRow] = []
    for row_num, cells in enumerate(all_rows, start=1):
        parsed = _apply_mapping(cells, payload.mapping, payload.designator_separator)
        if parsed.quantity_raw:
            fractional_rows.append(row_num)
        parsed_rows.append(parsed)

    if fractional_rows:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.BOM_FRACTIONAL_QUANTITY,
            f"BOM row(s) {fractional_rows} have fractional quantity values; "
            "only integer quantities are supported.",
            fractional_rows=fractional_rows,
        )

    inserted = matched = unmatched = 0
    for parsed in parsed_rows:
        if parsed.designators is None:
            parsed.designators = []
        # quantity default from designator count if not set
        if parsed.quantity == 1 and parsed.designators:
            parsed.quantity = len(parsed.designators)
        candidate = _match_part(db, workspace_id=workspace_id, row=parsed)
        entry_type = "part" if candidate else "unmatched"
        entry = ProjectEntry(
            workspace_id=workspace_id,
            project_id=project.id,
            entry_type=entry_type,
            part_id=candidate.id if candidate else None,
            name=parsed.part or (candidate.name if candidate else parsed.mpn) or "",
            quantity=parsed.quantity,
            comments=parsed.comments,
            designators=parsed.designators,
            cad_footprint=parsed.footprint,
            cad_key=parsed.cad_key,
            dnp=parsed.dnp,
            order_index=next_idx,
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(entry)
        next_idx += 1
        inserted += 1
        if candidate:
            matched += 1
        else:
            unmatched += 1
    db.flush()
    return BomImportCommitOut(inserted=inserted, matched=matched, unmatched=unmatched)

