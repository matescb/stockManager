"""BOM CSV/TSV importer per spec §10.6 + §16.3."""
from __future__ import annotations

import base64
import csv
import io
from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

import chardet
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

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


def _decode_b64(b64: str) -> bytes:
    return base64.b64decode(b64)


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
    quantity: float = 1.0
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


def _parse_quantity(s: str) -> float:
    s = (s or "").strip().replace(",", ".")
    if not s:
        return 1.0
    try:
        return float(s)
    except ValueError:
        return 1.0


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
            out.quantity = _parse_quantity(v)
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
    if payload.has_header and all_rows:
        all_rows = all_rows[1:]

    # Find next order_index
    next_idx = db.execute(
        select(func.coalesce(func.max(ProjectEntry.order_index), -1))
        .where(ProjectEntry.workspace_id == workspace_id)
        .where(ProjectEntry.project_id == project.id)
    ).scalar_one() + 1

    inserted = matched = unmatched = 0
    for cells in all_rows:
        parsed = _apply_mapping(cells, payload.mapping, payload.designator_separator)
        if parsed.designators is None:
            parsed.designators = []
        # quantity default from designator count if not set
        if (parsed.quantity == 1.0) and parsed.designators:
            parsed.quantity = float(len(parsed.designators))
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
