from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

import os

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from app.api.routes._activity import build_activity
from app.core.config import settings
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part, PartMetaMember, PartSubstitute
from app.domain.parts.providers import make_provider
from app.domain.parts.services.assets import fetch_provider_asset
from app.domain.stock.models import StockEntry
from app.domain.stock.schemas import AddStockIn, LotInput
from app.domain.stock.service import (
    StockError,
    add_stock,
    reserved_quantity,
    stock_summary_for_part,
    total_for_part,
)
from app.domain.workspaces.models import Workspace

router = APIRouter()


class PartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_type: Literal["linked", "local", "meta", "sub_assembly"] = "local"
    # Optional — defaults to mpn server-side when blank, so the operator can
    # paste an MPN and skip the name field. At least one of name/mpn must be
    # set; the create endpoint enforces that explicitly.
    name: str | None = Field(default=None, max_length=300)
    manufacturer: str | None = None
    mpn: str | None = None
    internal_part_number: str | None = None
    description: str | None = None
    notes_markdown: str | None = None
    footprint: str | None = None
    low_stock_report_quantity: int | None = None
    attrition_percentage: float = 0
    attrition_min_quantity: int = 0
    default_storage_location_id: UUID | None = None
    default_storage_mandatory: bool = False
    serialized: bool = False


class PartPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    manufacturer: str | None = None
    mpn: str | None = None
    internal_part_number: str | None = None
    description: str | None = None
    notes_markdown: str | None = None
    footprint: str | None = None
    low_stock_report_quantity: int | None = None
    attrition_percentage: float | None = None
    attrition_min_quantity: int | None = None
    default_storage_location_id: UUID | None = None
    default_storage_mandatory: bool | None = None
    serialized: bool | None = None
    published: bool | None = None
    # Command flag: when true, drops the provider link, clears
    # last_refresh_at, resets description_locally_edited, and converts
    # every {provider, override} custom_field row on this part to
    # `manual` (override rows lose their original_value).
    unlink_provider: bool | None = None


def _image_urls_for_parts(db, ws_id, part_ids: list) -> dict:
    """Single-shot SELECT for the per-part image_url custom_field row,
    keyed by part_id. The /parts list endpoint uses this so we don't
    fire one query per row."""
    if not part_ids:
        return {}
    rows = db.execute(
        select(CustomField.object_id, CustomField.value)
        .where(CustomField.workspace_id == ws_id)
        .where(CustomField.object_type == "part")
        .where(CustomField.object_id.in_(part_ids))
        .where(CustomField.key == "image_url")
    ).all()
    return {pid: val for pid, val in rows}


def _serialize(
    p: Part,
    *,
    on_hand: int | None = None,
    reserved: int | None = None,
    available: int | None = None,
    image_url: str | None = None,
) -> dict:
    if reserved is None:
        reserved = 0
    if available is None and on_hand is not None:
        available = on_hand - reserved
    return {
        "id": str(p.id),
        "part_type": p.part_type,
        "name": p.name,
        "manufacturer": p.manufacturer,
        "mpn": p.mpn,
        "internal_part_number": p.internal_part_number,
        "description": p.description,
        "footprint": p.footprint,
        "notes_markdown": p.notes_markdown,
        "low_stock_report_quantity": p.low_stock_report_quantity,
        "attrition_percentage": float(p.attrition_percentage or 0),
        "attrition_min_quantity": p.attrition_min_quantity or 0,
        "default_storage_location_id": str(p.default_storage_location_id) if p.default_storage_location_id else None,
        "default_storage_mandatory": p.default_storage_mandatory,
        "serialized": p.serialized,
        "published": bool(p.published),
        "linked_provider": p.linked_provider,
        "linked_external_id": p.linked_external_id,
        "last_refresh_at": p.last_refresh_at.isoformat() if p.last_refresh_at else None,
        "description_locally_edited": bool(p.description_locally_edited),
        "archived_at": p.archived_at.isoformat() if p.archived_at else None,
        "on_hand": on_hand,
        "reserved": reserved,
        "available": available if available is not None else 0,
        # Sourced from the part's `image_url` custom_field — the post-
        # download local path or a fallback to the upstream URL when the
        # download failed. None when no image was ever attached.
        "image_url": image_url,
    }


# ---------------------------------------------------------------------------
# Provider assets (images + datasheets, downloaded at part-creation /
# refresh time and served from our own origin so the app keeps working
# when the upstream CDN rotates a URL or goes down).
#
# Files live at {UPLOAD_DIR}/parts/{ws_id}/{sha256}.{ext} — content-
# addressed, so the immutable cache header is safe and overwrites can't
# break in-flight requests.
# ---------------------------------------------------------------------------


@router.get("/assets/{ws_id}/{filename}")
def get_provider_asset(
    ws_id: UUID,
    filename: str,
    ws: CurrentWorkspace,
    name: str | None = Query(default=None, max_length=120),
):
    # Workspace-scoped: an operator can only fetch assets that live under
    # their workspace's folder. The `ws` dep already proves membership in
    # the request's current workspace; this matches them.
    if ws_id != ws.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    # Disallow path traversal — filename must be a flat content-addressed name.
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid filename")

    abs_path = os.path.join(settings().UPLOAD_DIR, "parts", str(ws_id), filename)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")

    headers = {
        # Content-addressed → safe to cache for a year, never re-revalidate.
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    if name:
        # `inline` keeps PDF / image preview working; the filename only
        # comes into play when the user does Save As. Restrict to a safe
        # subset and append the original extension so the saved file
        # opens in the right viewer.
        safe = "".join(c for c in name if c.isalnum() or c in "._-")[:80] or "datasheet"
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        ext_suffix = f".{ext}" if ext and not safe.lower().endswith(f".{ext.lower()}") else ""
        headers["Content-Disposition"] = f'inline; filename="{safe}{ext_suffix}"'
    return FileResponse(abs_path, headers=headers)


@router.get("")
def list_parts(
    db: DbSession,
    ws: CurrentWorkspace,
    q: str | None = Query(default=None),
    archived: bool = Query(default=False),
    mpn: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
):
    stmt = select(Part).where(Part.workspace_id == ws.id)
    stmt = stmt.where(Part.archived_at.is_(None) if not archived else Part.archived_at.is_not(None))
    if mpn:
        stmt = stmt.where(Part.mpn == mpn)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Part.name.ilike(like),
                Part.mpn.ilike(like),
                Part.manufacturer.ilike(like),
                Part.internal_part_number.ilike(like),
                Part.description.ilike(like),
            )
        )
    stmt = stmt.order_by(Part.name).limit(limit)
    parts = list(db.execute(stmt).scalars())
    image_urls = _image_urls_for_parts(db, ws.id, [p.id for p in parts])
    out = []
    for p in parts:
        on_hand = total_for_part(db, workspace_id=ws.id, part_id=p.id)
        reserved = reserved_quantity(db, workspace_id=ws.id, part_id=p.id)
        out.append(_serialize(
            p,
            on_hand=on_hand,
            reserved=reserved,
            image_url=image_urls.get(p.id),
        ))
    return ok(out)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_part(payload: PartIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    # Name defaults to MPN when blank — paste-an-MPN-and-go workflow.
    # At least one of the two has to be set; the partial unique index on
    # (workspace_id, mpn) enforces no-duplicate-MPN at the DB level, but
    # we pre-check here so the response can name the existing part.
    name = (payload.name or "").strip()
    mpn = (payload.mpn or "").strip()
    if not name and not mpn:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="provide at least one of `name` or `mpn`",
        )
    if not name:
        name = mpn

    if mpn:
        existing = (
            db.query(Part)
            .filter(Part.workspace_id == ws.id, Part.mpn == mpn, Part.archived_at.is_(None))
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"MPN already used by part \"{existing.name}\"",
                    "existing_id": str(existing.id),
                    "existing_name": existing.name,
                },
            )

    p = Part(
        workspace_id=ws.id,
        part_type=payload.part_type,
        name=name,
        manufacturer=payload.manufacturer,
        mpn=mpn or None,
        internal_part_number=payload.internal_part_number,
        description=payload.description,
        notes_markdown=payload.notes_markdown,
        footprint=payload.footprint,
        low_stock_report_quantity=payload.low_stock_report_quantity,
        attrition_percentage=payload.attrition_percentage,
        attrition_min_quantity=payload.attrition_min_quantity,
        default_storage_location_id=payload.default_storage_location_id,
        default_storage_mandatory=payload.default_storage_mandatory,
        serialized=payload.serialized,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(p)
    db.commit()
    return ok(_serialize(p, on_hand=0, reserved=0))


def _get_part(db, ws_id, part_id) -> Part:
    p = db.get(Part, part_id)
    if not p or p.workspace_id != ws_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="part not found")
    return p


@router.get("/{part_id}")
def get_part(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    on_hand = total_for_part(db, workspace_id=ws.id, part_id=p.id)
    reserved = reserved_quantity(db, workspace_id=ws.id, part_id=p.id)
    image_url = _image_urls_for_parts(db, ws.id, [p.id]).get(p.id)
    return ok(_serialize(p, on_hand=on_hand, reserved=reserved, image_url=image_url))


@router.patch("/{part_id}")
def patch_part(part_id: UUID, payload: PartPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = _get_part(db, ws.id, part_id)
    data = payload.model_dump(exclude_unset=True)
    unlink = bool(data.pop("unlink_provider", False))

    # Linked-part guards: manufacturer + MPN are provider-owned for as long
    # as the link is active. The user must explicitly unlink to edit them.
    if p.linked_provider and not unlink:
        for f in ("manufacturer", "mpn"):
            if f in data and data[f] != getattr(p, f):
                raise HTTPException(
                    status_code=400,
                    detail=f"{f} is provider-owned on a linked part; pass unlink_provider=true to take ownership",
                )

    # Editing description on a linked part flips the locally-edited flag so
    # subsequent provider refreshes won't overwrite the user's wording.
    if (
        "description" in data
        and p.linked_provider is not None
        and data["description"] != p.description
    ):
        p.description_locally_edited = True

    for k, v in data.items():
        setattr(p, k, v)
    p.updated_by = user.id

    if unlink:
        p.linked_provider = None
        p.last_refresh_at = None
        p.description_locally_edited = False
        # Convert every provider/override custom_field on this part into a
        # plain manual row, dropping the saved original.
        rows = list(
            db.execute(
                select(CustomField)
                .where(CustomField.workspace_id == ws.id)
                .where(CustomField.object_type == "part")
                .where(CustomField.object_id == p.id)
                .where(CustomField.source.in_(["provider", "override"]))
            ).scalars()
        )
        for r in rows:
            r.source = "manual"
            r.original_value = None
            r.updated_by = user.id

    db.commit()
    return ok(
        _serialize(
            p,
            on_hand=total_for_part(db, workspace_id=ws.id, part_id=p.id),
            reserved=reserved_quantity(db, workspace_id=ws.id, part_id=p.id),
        )
    )


@router.post("/{part_id}/archive")
def archive_part(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    p.archived_at = datetime.now(timezone.utc)
    db.commit()
    return ok(None, "archived")


@router.post("/{part_id}/restore")
def restore_part(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    p.archived_at = None
    db.commit()
    return ok(None, "restored")


class BulkDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 100 is plenty for a single-shot multi-select; if a user wants to wipe
    # more they can run it twice. Keeps the response payload bounded.
    part_ids: list[UUID] = Field(min_length=1, max_length=100)


@router.post("/bulk-delete")
def bulk_delete_parts(payload: BulkDeleteIn, db: DbSession, ws: CurrentWorkspace):
    """Soft-delete (archive) the listed parts in one shot. Hard-deleting
    would foreign-key-cascade into stock_entries / lots / order_entries
    / bom_entries — the user can already filter `/parts/archived` to
    review or restore. Workspace-scoped: ids that don't belong are
    silently skipped (no information leak about other workspaces)."""
    now = datetime.now(timezone.utc)
    rows = (
        db.query(Part)
        .filter(Part.workspace_id == ws.id, Part.id.in_(payload.part_ids))
        .all()
    )
    archived_ids = []
    for p in rows:
        if p.archived_at is None:
            p.archived_at = now
            archived_ids.append(str(p.id))
    db.commit()
    return ok(
        {
            "archived_ids": archived_ids,
            "skipped": len(payload.part_ids) - len(archived_ids),
        },
        f"archived {len(archived_ids)}",
    )


@router.get("/by-bag-signature/{signature}")
def find_by_bag_signature(signature: str, db: DbSession, ws: CurrentWorkspace):
    """Look up the most recent stock_entry whose bag_signature matches.
    Used by the scan-import UI: when the operator scans the same physical
    bag again, the frontend hits this endpoint to surface the prior
    import inline instead of waiting for the bulk-import POST to come
    back with a `bag_rescan` status."""
    if len(signature) != 64 or not signature.isalnum():
        # SHA-256 hex digest is exactly 64 hex chars. Reject obvious junk
        # so this can't be abused for prefix-scan probing.
        return ok(None)
    prior = db.execute(
        select(StockEntry)
        .where(StockEntry.workspace_id == ws.id)
        .where(StockEntry.bag_signature == signature)
        .order_by(StockEntry.occurred_at.desc())
        .limit(1)
    ).scalars().first()
    if prior is None:
        return ok(None)
    return ok({
        "part_id": str(prior.part_id),
        "lot_id": str(prior.lot_id) if prior.lot_id else None,
        "storage_location_id": (
            str(prior.storage_location_id) if prior.storage_location_id else None
        ),
        "quantity": int(prior.quantity_delta or 0),
    })


@router.get("/{part_id}/stock")
def part_stock(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    rows = stock_summary_for_part(db, workspace_id=ws.id, part_id=p.id)
    return ok(
        {
            "total_on_hand": total_for_part(db, workspace_id=ws.id, part_id=p.id),
            "rows": [
                {
                    "storage_location_id": str(r["storage_location_id"]) if r["storage_location_id"] else None,
                    "lot_id": str(r["lot_id"]) if r["lot_id"] else None,
                    "quantity": r["quantity"],
                }
                for r in rows
            ],
        }
    )


@router.get("/{part_id}/lots")
def part_lots(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    from app.domain.lots.models import Lot
    p = _get_part(db, ws.id, part_id)
    lots = list(
        db.execute(
            select(Lot).where(Lot.workspace_id == ws.id).where(Lot.part_id == p.id).order_by(Lot.created_at.desc())
        ).scalars()
    )
    return ok(
        [
            {
                "id": str(l.id),
                "name": l.name,
                "serial_number": l.serial_number,
                "purchase_quantity": l.purchase_quantity,
                "purchase_unit_cost": float(l.purchase_unit_cost) if l.purchase_unit_cost is not None else None,
                "purchase_currency": l.purchase_currency,
                "expiration_date": l.expiration_date.isoformat() if l.expiration_date else None,
                "comments": l.comments,
                "parent_lot_id": str(l.parent_lot_id) if l.parent_lot_id else None,
                "source_type": l.source_type,
                "created_at": l.created_at.isoformat(),
            }
            for l in lots
        ]
    )


class SubstituteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    substitute_part_id: UUID
    direction: Literal["one_way", "bidirectional"] = "bidirectional"


@router.post("/{part_id}/substitutes")
def add_substitute(part_id: UUID, payload: SubstituteIn, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    sub = _get_part(db, ws.id, payload.substitute_part_id)
    db.add(PartSubstitute(part_id=p.id, substitute_part_id=sub.id, direction=payload.direction))
    db.commit()
    return ok(None)


@router.get("/{part_id}/substitutes")
def list_substitutes(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    rows = list(db.execute(select(PartSubstitute).where(PartSubstitute.part_id == p.id)).scalars())
    return ok([{"part_id": str(r.substitute_part_id), "direction": r.direction} for r in rows])


@router.delete("/{part_id}/substitutes/{substitute_id}")
def del_substitute(part_id: UUID, substitute_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    db.query(PartSubstitute).filter(
        PartSubstitute.part_id == p.id, PartSubstitute.substitute_part_id == substitute_id
    ).delete()
    db.commit()
    return ok(None)


# ---- Meta-part members ----------------------------------------------------


class MetaMemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_part_id: UUID


@router.get("/{meta_id}/members")
def list_members(meta_id: UUID, db: DbSession, ws: CurrentWorkspace):
    meta = _get_part(db, ws.id, meta_id)
    rows = list(
        db.execute(
            select(PartMetaMember).where(PartMetaMember.meta_part_id == meta.id)
        ).scalars()
    )
    return ok([{"id": str(r.id), "member_part_id": str(r.part_id)} for r in rows])


@router.post("/{meta_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(meta_id: UUID, payload: MetaMemberIn, db: DbSession, ws: CurrentWorkspace):
    meta = _get_part(db, ws.id, meta_id)
    if meta.part_type != "meta":
        raise HTTPException(status_code=400, detail="part is not a meta-part")
    member = _get_part(db, ws.id, payload.member_part_id)
    if member.id == meta.id:
        raise HTTPException(status_code=400, detail="meta-part cannot include itself")
    if member.part_type == "meta":
        raise HTTPException(status_code=400, detail="meta-part members cannot themselves be meta")
    existing = (
        db.execute(
            select(PartMetaMember)
            .where(PartMetaMember.meta_part_id == meta.id)
            .where(PartMetaMember.part_id == member.id)
        )
        .scalars()
        .first()
    )
    if existing:
        return ok({"id": str(existing.id), "member_part_id": str(existing.part_id)})
    row = PartMetaMember(meta_part_id=meta.id, part_id=member.id)
    db.add(row)
    db.commit()
    return ok({"id": str(row.id), "member_part_id": str(row.part_id)})


@router.delete("/{meta_id}/members/{member_id}")
def del_member(meta_id: UUID, member_id: UUID, db: DbSession, ws: CurrentWorkspace):
    meta = _get_part(db, ws.id, meta_id)
    db.query(PartMetaMember).filter(
        PartMetaMember.meta_part_id == meta.id, PartMetaMember.part_id == member_id
    ).delete()
    db.commit()
    return ok(None, "deleted")


@router.get("/{part_id}/activity")
def part_activity(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    stock_rows = list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == ws.id)
            .where(StockEntry.part_id == p.id)
            .order_by(StockEntry.occurred_at.desc())
            .limit(200)
        ).scalars()
    )
    events = build_activity(
        db,
        stock_rows=stock_rows,
        created_at=p.created_at,
        updated_at=p.updated_at,
        created_by=p.created_by,
        updated_by=p.updated_by,
        created_kind="part_created",
        updated_kind="part_updated",
    )
    return ok(events)


# Reserved keys that surface elsewhere on PartInfo (Media card). These
# are also treated as `source='provider'` rows but we keep them out of
# the spec body when listing.
_PROVIDER_RESERVED_KEYS = ("image_url", "datasheet_url")


@router.post("/{part_id}/refresh-from-provider")
def refresh_from_provider(
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Re-run the workspace's configured MPN lookup against this part's
    stored MPN. Reconciles `source='provider'` custom_field rows
    (insert / update / delete) and never touches `manual` / `override`.
    Updates manufacturer + mpn + footprint always; description only when
    it hasn't been locally edited."""
    p = _get_part(db, ws.id, part_id)
    if not (p.mpn or "").strip():
        raise HTTPException(status_code=400, detail="part has no MPN to look up")

    provider = make_provider(
        ws.parts_provider,
        ws.parts_provider_api_key,
        ws.parts_provider_api_secret,
    )
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail="no parts provider configured (set one in Workspace settings)",
        )

    out = provider.lookup_mpn(p.mpn.strip())
    if not out.get("found") or not out.get("result"):
        return ok(
            {
                "found": False,
                "message": out.get("message") or "no match",
                "provider": provider.name,
            }
        )

    r = out["result"]
    p.manufacturer = r.get("manufacturer") or p.manufacturer
    new_mpn = r.get("mpn") or p.mpn
    if new_mpn:
        p.mpn = new_mpn
    fp = r.get("footprint")
    if fp:
        # On every refresh we let the provider drive footprint — same
        # treatment as manufacturer/mpn (provider-owned for linked parts).
        p.footprint = fp
    if not p.description_locally_edited:
        new_desc = r.get("description")
        if new_desc:
            p.description = new_desc
    p.linked_provider = provider.name
    p.linked_external_id = r.get("mpn") or p.linked_external_id
    p.last_refresh_at = datetime.now(timezone.utc)
    p.updated_by = user.id

    # Reconcile spec rows. For each provider-supplied (key, value):
    #   • existing row, source='provider'  → update value
    #   • existing row, source='manual'    → leave alone (user owns it)
    #   • existing row, source='override'  → leave alone, but remember the
    #     new upstream value as the new `original_value` so a Restore
    #     reflects current upstream, not historical.
    #   • absent                           → insert with source='provider'
    # After processing, any source='provider' row whose key isn't in the
    # upstream payload (and isn't a reserved system key) is deleted.
    desired: dict[str, str] = {}
    for s in r.get("specs") or []:
        key = (s.get("key") or "").strip()
        value = (s.get("value") or "").strip()
        if key:
            desired[key] = value
    # Download provider assets locally — same fallback semantics as
    # bulk-import: failed downloads keep the upstream URL.
    if r.get("image_url"):
        local = fetch_provider_asset(r["image_url"], str(ws.id), "image")
        desired["image_url"] = local or r["image_url"]
    if r.get("datasheet_url"):
        local = fetch_provider_asset(r["datasheet_url"], str(ws.id), "datasheet")
        desired["datasheet_url"] = local or r["datasheet_url"]

    existing_rows = list(
        db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == ws.id)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == p.id)
        ).scalars()
    )
    by_key = {row.key: row for row in existing_rows}

    added = updated = removed = 0
    for key, value in desired.items():
        row = by_key.get(key)
        if row is None:
            db.add(
                CustomField(
                    workspace_id=ws.id,
                    object_type="part",
                    object_id=p.id,
                    key=key,
                    value=value,
                    source="provider",
                    created_by=user.id,
                    updated_by=user.id,
                )
            )
            added += 1
        elif row.source == "provider":
            if row.value != value:
                row.value = value
                row.updated_by = user.id
                updated += 1
        elif row.source == "override":
            # Refresh the saved baseline so the Restore button reverts to
            # the current upstream value — not what was sent the first
            # time the part was linked.
            if row.original_value != value:
                row.original_value = value
                row.updated_by = user.id

    upstream_keys = set(desired.keys())
    for row in existing_rows:
        if row.source == "provider" and row.key not in upstream_keys:
            db.delete(row)
            removed += 1

    db.commit()
    return ok(
        {
            "found": True,
            "provider": provider.name,
            "summary": {
                "added": added,
                "updated": updated,
                "removed": removed,
            },
            "part": _serialize(
                p,
                on_hand=total_for_part(db, workspace_id=ws.id, part_id=p.id),
                reserved=reserved_quantity(db, workspace_id=ws.id, part_id=p.id),
            ),
        }
    )


# ---------------------------------------------------------------------------
# Bulk import from a barcode-scan session.
#
# The frontend's scan-import flow accumulates rows of {mpn, quantity?,
# storage_location_id?} as bags are scanned. Each row goes through the
# same MPN→provider→canonical-record pipeline used by lookup-mpn, then
# we materialise: a Part (linked-type), `source='provider'` custom_fields
# for every spec, and — if quantity>0 and a storage location is given —
# a stock entry. The endpoint returns one status row per input so the
# UI can show a per-row outcome banner ("created", "duplicate", "no match").
# ---------------------------------------------------------------------------


class ScanImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpn: str = Field(min_length=1, max_length=200)
    quantity: int | None = Field(default=None, ge=0)
    storage_location_id: UUID | None = None
    # Traceability fields lifted from the bag's 2D code. The frontend
    # synthesises these strings from the parsed DIs (10D/1T → lot_name,
    # K/1K/14K/11K → comments). All optional — the import works without
    # them, you just lose the audit trail.
    lot_name: str | None = Field(default=None, max_length=200)
    lot_serial: str | None = Field(default=None, max_length=200)
    comments: str | None = Field(default=None, max_length=1000)
    # sha256 hex of the normalised raw bag code. When the workspace has
    # already imported a bag with this signature, the row resolves with
    # status='bag_rescan' carrying the prior import's part/lot/location/qty
    # so the frontend can offer an inline "remove qty from this bag"
    # affordance instead of double-importing.
    bag_signature: str | None = Field(default=None, max_length=64)


class ScanImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[ScanImportRow] = Field(min_length=1, max_length=200)


@router.post("/bulk-import-from-scan")
def bulk_import_from_scan(
    payload: ScanImportIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Materialise scanned bag rows into Parts (+ optional initial stock).
    Each row is independent — duplicates / no-match outcomes are returned
    inline rather than aborting the batch."""
    provider = make_provider(
        ws.parts_provider,
        ws.parts_provider_api_key,
        ws.parts_provider_api_secret,
    )
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail="no parts provider configured (set one in Workspace settings)",
        )

    out_rows: list[dict] = []
    for row in payload.rows:
        mpn = (row.mpn or "").strip()
        if not mpn:
            out_rows.append({
                "mpn": row.mpn,
                "status": "invalid",
                "error": "empty MPN",
            })
            continue

        # Bag re-scan recognition — same physical bag scanned again.
        # The first import wrote bag_signature on the resulting
        # stock_entry; finding it now means we should offer the operator
        # a path to consume from this lot rather than double-importing.
        if row.bag_signature:
            prior = db.execute(
                select(StockEntry)
                .where(StockEntry.workspace_id == ws.id)
                .where(StockEntry.bag_signature == row.bag_signature)
                .order_by(StockEntry.occurred_at.desc())
                .limit(1)
            ).scalars().first()
            if prior is not None:
                out_rows.append({
                    "mpn": mpn,
                    "status": "bag_rescan",
                    "part_id": str(prior.part_id),
                    "lot_id": str(prior.lot_id) if prior.lot_id else None,
                    "storage_location_id": (
                        str(prior.storage_location_id) if prior.storage_location_id else None
                    ),
                    "quantity": int(prior.quantity_delta or 0),
                })
                continue

        # Duplicate check — workspace-scoped, case-sensitive (mirrors how
        # GET /parts?mpn= matches).
        existing = db.execute(
            select(Part)
            .where(Part.workspace_id == ws.id)
            .where(Part.mpn == mpn)
            .where(Part.archived_at.is_(None))
            .limit(1)
        ).scalars().first()
        if existing is not None:
            out_rows.append({
                "mpn": mpn,
                "status": "duplicate",
                "part_id": str(existing.id),
            })
            continue

        # Provider lookup.
        try:
            lookup = provider.lookup_mpn(mpn)
        except Exception as exc:  # provider should swallow these — belt+braces
            out_rows.append({
                "mpn": mpn,
                "status": "lookup_failed",
                "error": f"provider raised {type(exc).__name__}",
            })
            continue
        if not lookup.get("found") or not lookup.get("result"):
            out_rows.append({
                "mpn": mpn,
                "status": "lookup_failed",
                "error": lookup.get("message") or "no match",
            })
            continue

        r = lookup["result"]
        # Name: description if we have it, else MPN. Both providers
        # typically return a useful description.
        name = (r.get("description") or "").strip() or mpn
        # Truncate to the column limit (Part.name is varchar(300)).
        if len(name) > 300:
            name = name[:300]

        p = Part(
            workspace_id=ws.id,
            part_type="linked",
            name=name,
            manufacturer=(r.get("manufacturer") or None),
            mpn=(r.get("mpn") or mpn),
            description=(r.get("description") or None),
            footprint=(r.get("footprint") or None),
            attrition_percentage=0,
            attrition_min_quantity=0,
            default_storage_location_id=row.storage_location_id,
            default_storage_mandatory=False,
            serialized=False,
            linked_provider=provider.name,
            linked_external_id=(r.get("mpn") or mpn),
            last_refresh_at=datetime.now(timezone.utc),
            description_locally_edited=False,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(p)
        db.flush()  # assign p.id for the custom_fields below

        # Materialise spec rows + image/datasheet as `source='provider'`,
        # mirroring the refresh-from-provider path. Skip empties.
        for s in (r.get("specs") or []):
            key = (s.get("key") or "").strip()
            value = (s.get("value") or "").strip()
            if not key or not value:
                continue
            db.add(CustomField(
                workspace_id=ws.id,
                object_type="part",
                object_id=p.id,
                key=key,
                value=value,
                source="provider",
                created_by=user.id,
                updated_by=user.id,
            ))
        # Download image + datasheet locally so we don't depend on the
        # provider's CDN at render time. Failed downloads fall back to
        # the original URL so the worst case is unchanged from before.
        if r.get("image_url"):
            local = fetch_provider_asset(r["image_url"], str(ws.id), "image")
            db.add(CustomField(
                workspace_id=ws.id,
                object_type="part",
                object_id=p.id,
                key="image_url",
                value=local or r["image_url"],
                source="provider",
                created_by=user.id,
                updated_by=user.id,
            ))
        if r.get("datasheet_url"):
            local = fetch_provider_asset(r["datasheet_url"], str(ws.id), "datasheet")
            db.add(CustomField(
                workspace_id=ws.id,
                object_type="part",
                object_id=p.id,
                key="datasheet_url",
                value=local or r["datasheet_url"],
                source="provider",
                created_by=user.id,
                updated_by=user.id,
            ))

        # Initial stock entry — when the bag's Q field carries a count
        # (or the operator entered one), the part lands on-hand right
        # away. Storage location is optional: when present, the entry is
        # filed there; when absent, it's recorded with no location so the
        # operator can move/file it later from the Stock view. This
        # mirrors how a freshly-arrived bag actually exists physically:
        # you have it in hand, the count is on the label, the bin
        # assignment can happen later.
        qty_added = 0
        stock_error: str | None = None
        if row.quantity and row.quantity > 0:
            # Build a Lot row only when the bag carried any traceability
            # info. Without it, add_stock makes a bare stock entry with
            # no associated lot — same as the manual stock-add flow.
            lot_payload: LotInput | None = None
            if row.lot_name or row.lot_serial:
                lot_payload = LotInput(
                    name=row.lot_name,
                    serial_number=row.lot_serial,
                )
            try:
                add_stock(
                    db,
                    workspace_id=ws.id,
                    user_id=user.id,
                    payload=AddStockIn(
                        part_id=p.id,
                        quantity=row.quantity,
                        storage_location_id=row.storage_location_id,
                        lot=lot_payload,
                        comments=row.comments,
                        bag_signature=row.bag_signature,
                    ),
                )
                qty_added = row.quantity
            except StockError as exc:
                # Don't fail the whole row — the part is created, but
                # surface the stock issue so the UI can flag it.
                stock_error = str(exc)

        out_rows.append({
            "mpn": mpn,
            "status": "created",
            "part_id": str(p.id),
            "quantity_added": qty_added,
            "stock_error": stock_error,
        })

    db.commit()
    summary = {
        "created":        sum(1 for r in out_rows if r["status"] == "created"),
        "duplicate":      sum(1 for r in out_rows if r["status"] == "duplicate"),
        "bag_rescan":     sum(1 for r in out_rows if r["status"] == "bag_rescan"),
        "lookup_failed":  sum(1 for r in out_rows if r["status"] == "lookup_failed"),
        "invalid":        sum(1 for r in out_rows if r["status"] == "invalid"),
    }
    return ok({"rows": out_rows, "summary": summary, "provider": provider.name})


class QuickRemoveBagIn(BaseModel):
    """Inline-consume from a recognised re-scanned bag. The frontend
    sends back the lot/location/qty hint it got from the bag_rescan
    row in /bulk-import-from-scan; we run remove_stock with those
    coordinates targeting the named lot+location."""
    model_config = ConfigDict(extra="forbid")

    quantity: int = Field(gt=0)
    lot_id: UUID | None = None
    storage_location_id: UUID | None = None
    comments: str | None = Field(default=None, max_length=1000)


@router.post("/{part_id}/quick-remove-bag")
def quick_remove_bag(
    part_id: UUID,
    payload: QuickRemoveBagIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Remove `quantity` units from a specific (lot, location) combo —
    used by the scan-import re-scan UI to consume from a bag that was
    imported earlier without forcing the operator into the full
    Remove-Stock form. remove_stock enforces the lot's actual on-hand
    via the same path as the manual flow, so an over-qty request
    still 4xx's cleanly."""
    p = _get_part(db, ws.id, part_id)
    from app.domain.stock.schemas import RemoveStockIn
    from app.domain.stock.service import remove_stock as _remove
    try:
        _remove(
            db,
            workspace_id=ws.id,
            user_id=user.id,
            payload=RemoveStockIn(
                part_id=p.id,
                quantity=payload.quantity,
                storage_location_id=payload.storage_location_id,
                lot_id=payload.lot_id,
                comments=payload.comments,
            ),
        )
    except StockError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return ok(None, "removed")
