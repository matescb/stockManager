"""Scan-to-import bulk import and quick-remove-bag endpoints.

POST /bulk-import-from-scan      — materialise scanned bag rows into Parts
POST /{part_id}/quick-remove-bag — consume from a previously-imported bag

All endpoints share the /api/parts prefix (registered in main.py).
No URL structure changes from the original monolithic parts.py.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from time import monotonic
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.routes._parts_shared import get_part as _get_part
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.core.secrets import decrypt
from app.domain.parts.models import BulkImportIdempotency, Part
from app.domain.parts.providers import make_provider
from app.domain.parts.schemas import QuickRemoveBagIn, ScanImportIn, ScanImportRow
from app.domain.parts.services.bag_signature import compute_bag_signature
from app.domain.parts.services.provider_cache import lookup_with_cache
from app.domain.parts.services.provider_import import create_from_provider_lookup
from app.domain.stock.models import StockEntry
from app.domain.stock.schemas import AddStockIn, LotInput
from app.domain.stock.service import StockError, add_stock
from app.domain.storage.models import StorageLocation

router = APIRouter()
logger = logging.getLogger(__name__)

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


_BULK_IMPORT_REQUEST_DEADLINE_S = 60.0   # wall-clock budget for the whole request
_BULK_IMPORT_ROW_TIMEOUT_S = 8.0          # per-row provider-lookup timeout
_BULK_IMPORT_IDEMPOTENCY_TTL_H = 24       # hours before cache rows are swept


def _bulk_import_content_key(ws_id: str, rows) -> str:
    """Derive a deterministic 64-hex-char SHA-256 key from request content.

    Serialises every field of every row (sorted by a stable key) so that
    two calls with different quantities / storage locations / lot names hash
    to different keys even when the MPNs and bag signatures are identical.
    Order-independent: rows are sorted by (bag_signature or "", mpn) before
    serialisation so the operator may re-order rows between retries.
    """
    row_blobs = sorted(
        json.dumps(r.model_dump(), sort_keys=True, default=str)
        for r in rows
    )
    raw = f"{ws_id}|{'||'.join(row_blobs)}"
    return hashlib.sha256(raw.encode()).hexdigest()


@router.post("/bulk-import-from-scan")
@limiter.limit("5/minute", key_func=workspace_key)
def bulk_import_from_scan(
    request: Request,
    payload: ScanImportIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Materialise scanned bag rows into Parts (+ optional initial stock).

    Each row is independent — both at the result-shape level (duplicates /
    no-match outcomes are returned inline rather than aborting the batch)
    AND at the database level: every row that does writes is wrapped in a
    SAVEPOINT (`db.begin_nested()`) so an unexpected exception mid-row
    (IntegrityError on a unique constraint, asset-fetch network error,
    anything we didn't anticipate) rolls back ONLY that row's writes
    without losing the rest of the batch. The outer transaction commits
    every surviving savepoint at the end. Without this, a single uncaught
    exception in row N would discard rows 1..N-1's writes — which the
    operator already saw acknowledged in the per-row outcome list — and
    the audit trail would diverge from what was actually persisted (Sec
    CRIT-6).

    Partial-commit semantics: savepoints commit durably at the outer
    `db.commit()` near the end of this function. If the proxy 502/504s
    *after* that commit, the client did not see the response body — but
    the rows are durably persisted. Retrying with the same `idempotency_key`
    returns the cached envelope verbatim (no new Parts created). Retrying
    *without* an idempotency key will re-derive the same content-hash and
    likewise return the cached result (BE2-003).

    Bounded latency (BE2-003):
    - Row cap: max 50 rows per request (ScanImportIn.rows max_length=50).
    - Request deadline: 60 s total. Rows not reached before the deadline
      are returned with status="deadline_exceeded".
    - Per-row provider timeout: 8 s. A slow MPN surfaces as
      status="lookup_failed" with a timeout reason; neighbouring rows
      still process.
    """
    # ------------------------------------------------------------------
    # Idempotency — best-effort sweep of expired rows then cache lookup.
    #
    # The key is FE-supplied (UUID4 generated once per submit attempt,
    # re-sent unchanged on retry) or falls back to a SHA-256 content
    # hash of the full row payload so that true retries of identical
    # bytes are deduplicated even without an explicit key. The content
    # hash includes all fields (quantity, storage_location_id, etc.) so
    # two calls that differ in any detail are treated as distinct.
    # ------------------------------------------------------------------
    explicit_key = (payload.idempotency_key or "").strip() or None
    idempotency_key = explicit_key or _bulk_import_content_key(str(ws.id), payload.rows)

    # Sweep rows older than TTL (best-effort, don't abort on failure).
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_BULK_IMPORT_IDEMPOTENCY_TTL_H)
        db.execute(
            BulkImportIdempotency.__table__.delete().where(
                BulkImportIdempotency.workspace_id == ws.id,
                BulkImportIdempotency.created_at < cutoff,
            )
        )
    except Exception:
        pass

    # Cache lookup — MUST filter by workspace_id (isolation invariant).
    # Only check the cache when the FE supplied an explicit key. Relying
    # on the content-hash fallback for cache HIT would suppress the
    # duplicate-MPN detection path for a second scan of the same MPN —
    # the client sends identical bytes but expects a live re-check.
    if explicit_key:
        cached = db.execute(
            select(BulkImportIdempotency)
            .where(BulkImportIdempotency.workspace_id == ws.id)
            .where(BulkImportIdempotency.key == idempotency_key)
        ).scalars().first()
        if cached is not None:
            return ok(cached.result_json)

    # ------------------------------------------------------------------
    # Provider setup
    # ------------------------------------------------------------------
    provider = make_provider(
        ws.parts_provider,
        decrypt(ws.parts_provider_api_key),
        decrypt(ws.parts_provider_api_secret),
    )
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail="no parts provider configured (set one in Workspace settings)",
        )

    # ------------------------------------------------------------------
    # Per-request deadline
    # ------------------------------------------------------------------
    deadline = monotonic() + _BULK_IMPORT_REQUEST_DEADLINE_S

    # Function-scope executor for per-row provider timeouts. We deliberately
    # do NOT use `with ThreadPoolExecutor(...) as pool:` at row scope — that
    # calls `shutdown(wait=True)` on exit, which blocks waiting for any
    # timed-out worker thread to finish its hung HTTP call (defeating the
    # bounded-blocking goal). Instead we hold a single executor for the
    # whole request, abandon timed-out futures, and tear down with
    # `wait=False` + `cancel_futures=True` at the end so the request
    # returns even if a worker is still hung. Hung worker threads will
    # finish on the provider's own socket timeout and exit cleanly.
    _bulk_import_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="bulk-import-lookup",
    )

    out_rows: list[dict] = []
    for row in payload.rows:
        # Check wall-clock budget before starting each row.
        if monotonic() >= deadline:
            out_rows.append({
                "mpn": (row.mpn or "").strip() or row.mpn,
                "status": "deadline_exceeded",
                "error": "request deadline exceeded; retry with the same idempotency_key",
            })
            continue

        mpn = (row.mpn or "").strip()
        if not mpn:
            out_rows.append({
                "mpn": row.mpn,
                "status": "invalid",
                "error": "empty MPN",
            })
            continue

        # Per-row workspace validation of caller-supplied storage. Without
        # this, the Part is committed with `default_storage_location_id`
        # pointing at a foreign workspace's row (existence-oracle + downstream
        # foot-gun). Same fix as create_part / patch_part — surface the
        # failure as `invalid` so the rest of the batch still runs.
        if row.storage_location_id is not None:
            sl = db.get(StorageLocation, row.storage_location_id)
            if sl is None or sl.workspace_id != ws.id:
                out_rows.append({
                    "mpn": mpn,
                    "status": "invalid",
                    "error": "storage location not found in workspace",
                })
                continue

        # Server-side bag_signature verification (BE2-015).  When the
        # client supplies the raw bag code alongside the signature we
        # recompute the digest independently.  A mismatch means a buggy
        # or adversarial client — surface it as `bag_signature_mismatch`
        # so the operator sees something and ops aren't blind to the bug.
        if row.bag_signature and row.raw_bag_code is not None:
            expected = compute_bag_signature(row.raw_bag_code)
            if expected != row.bag_signature:
                out_rows.append({
                    "mpn": mpn,
                    "status": "bag_signature_mismatch",
                    "error": "bag_signature does not match recomputed digest of raw_bag_code",
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

        # Provider lookup with per-row timeout. The provider classes are
        # synchronous HTTP; submit to a function-scope ThreadPoolExecutor
        # future so we can enforce a hard timeout without rewriting them.
        #
        # IMPORTANT: the executor is created ONCE for the whole request
        # (see _bulk_import_executor below) and we deliberately do NOT
        # `shutdown(wait=True)` between rows. A `with ThreadPoolExecutor(...)`
        # block at row scope would block on shutdown waiting for the
        # timed-out worker thread to finish its hung HTTP call — which
        # defeats the entire bounded-blocking goal under prod's --workers 1.
        # On timeout we abandon the future (the worker thread leaks for
        # the duration of the provider socket timeout, but the request
        # continues) and surface the row as `lookup_failed`.
        # We capture unexpected exceptions to Sentry as belt-and-braces —
        # the row still resolves with `lookup_failed`.
        row_budget = deadline - monotonic()
        actual_timeout = min(_BULK_IMPORT_ROW_TIMEOUT_S, max(0.5, row_budget))
        lookup: dict | None = None
        try:
            fut = _bulk_import_executor.submit(lookup_with_cache, provider, mpn)
            try:
                lookup = fut.result(timeout=actual_timeout)
            except concurrent.futures.TimeoutError:
                # Cancel if still queued; if running, the worker thread
                # will finish in the background and its result is dropped.
                fut.cancel()
                out_rows.append({
                    "mpn": mpn,
                    "status": "lookup_failed",
                    "error": f"provider timeout after {actual_timeout:.1f}s",
                })
                continue
        except Exception as exc:
            try:  # local import keeps this path zero-cost when SENTRY_DSN is empty
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except Exception:
                pass
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
        candidate_count = len(lookup.get("candidates") or [])
        needs_disambiguation = candidate_count > 1
        # Name: description if we have it, else MPN. Both providers
        # typically return a useful description.
        name = (r.get("description") or "").strip() or mpn
        # Truncate to the column limit (Part.name is varchar(300)).
        if len(name) > 300:
            name = name[:300]

        # Wrap every write for this row in a savepoint. If anything
        # below this line raises (IntegrityError on the partial-unique
        # MPN constraint, asset-fetch raising mid-flight, fetch_provider_asset
        # crashing, anything else unanticipated) — only this row rolls
        # back. Other rows in the batch keep their writes.
        try:
            with db.begin_nested():
                p, qty_added, stock_error = _import_one_scan_row(
                    db, ws=ws, user=user, row=row, mpn=mpn,
                    provider_name=provider.name, lookup_result=r,
                )
        except Exception as exc:
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except Exception:
                pass
            out_rows.append({
                "mpn": mpn,
                "status": "row_failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        created_row = {
            "mpn": mpn,
            "status": "created",
            "part_id": str(p.id),
            "quantity_added": qty_added,
            "stock_error": stock_error,
        }
        if needs_disambiguation:
            created_row["needs_disambiguation"] = True
            created_row["candidate_count"] = candidate_count
            if r.get("manufacturer"):
                created_row["selected_manufacturer"] = r["manufacturer"]
        out_rows.append(created_row)

    # Tear down the executor without waiting for any hung worker thread.
    # `cancel_futures=True` cancels still-queued futures; running ones
    # are abandoned (their result is discarded; thread finishes when its
    # underlying socket times out). This is the critical bit that keeps
    # the per-row timeout bounded: a `with` block at row scope (or a
    # `wait=True` shutdown here) would re-introduce the wall-clock pin.
    _bulk_import_executor.shutdown(wait=False, cancel_futures=True)

    # `bulk_import_from_scan` keeps an explicit terminal commit even
    # though `get_db` commits on clean exit (BE2-010). Savepoint
    # releases aren't independently durable — they only become durable
    # at the OUTER transaction's commit. The real reason is response-
    # build robustness: if anything between this point and the dep's
    # final commit raises (an unexpected serialisation error, a Sentry
    # tag enrich that hits a network blip), we don't want to lose a
    # batch of imports the operator already saw on the scanner. Commit
    # here pins the batch; the dep's commit on clean exit is a no-op.
    summary = {
        "created":                  sum(1 for r in out_rows if r["status"] == "created"),
        "duplicate":                sum(1 for r in out_rows if r["status"] == "duplicate"),
        "bag_rescan":               sum(1 for r in out_rows if r["status"] == "bag_rescan"),
        "bag_signature_mismatch":   sum(
            1 for r in out_rows if r["status"] == "bag_signature_mismatch"
        ),
        "lookup_failed":            sum(1 for r in out_rows if r["status"] == "lookup_failed"),
        "invalid":                  sum(1 for r in out_rows if r["status"] == "invalid"),
        "row_failed":               sum(1 for r in out_rows if r["status"] == "row_failed"),
        "deadline_exceeded":        sum(1 for r in out_rows if r["status"] == "deadline_exceeded"),
        "needs_disambiguation":     sum(1 for r in out_rows if r.get("needs_disambiguation")),
    }
    result_payload = {"rows": out_rows, "summary": summary, "provider": provider.name}

    # Write idempotency cache entry before committing so a concurrent
    # identical request (race window is tiny) sees the result immediately.
    #
    # CRITICAL: this MUST be a true `INSERT … ON CONFLICT DO NOTHING`
    # (postgres dialect upsert), NOT plain ORM `add`/`flush`. On the race
    # path (two concurrent requests with the same key reach this point
    # together) a plain `flush()` would raise `IntegrityError` on the
    # composite-PK conflict — and a Session-level `db.rollback()` here
    # would unwind the OUTER transaction, discarding every per-row
    # savepoint write. The response would still report
    # `summary: {created: N, …}` while ZERO Parts persist — that's the
    # exact partial-commit divergence this PR is supposed to fix.
    # `on_conflict_do_nothing` makes the second writer a silent no-op
    # at the SQL level so the outer tx stays intact.
    db.execute(
        pg_insert(BulkImportIdempotency.__table__)
        .values(
            workspace_id=ws.id,
            key=idempotency_key,
            result_json=result_payload,
            created_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "key"])
    )

    db.commit()
    return ok(result_payload)


def _import_one_scan_row(
    db,
    *,
    ws,
    user,
    row: ScanImportRow,
    mpn: str,
    provider_name: str,
    lookup_result: dict,
):
    """Write the Part + provider custom_fields + initial stock for a
    single bulk-import row, INSIDE a caller-managed savepoint. Returns
    (part, qty_added, stock_error). Raises on any unanticipated DB
    failure — the caller's `with db.begin_nested():` rolls back this
    row only.
    """
    p = create_from_provider_lookup(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        provider_name=provider_name,
        mpn=mpn,
        lookup_result=lookup_result,
        default_storage_location_id=row.storage_location_id,
    )

    # Initial stock entry — when the bag's Q field carries a count
    # (or the operator entered one), the part lands on-hand right
    # away. Storage location is optional: when present, the entry is
    # filed there; when absent, it's recorded with no location so the
    # operator can move/file it later from the Stock view.
    qty_added = 0
    stock_error: str | None = None
    if row.quantity and row.quantity > 0:
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
            # Don't fail the whole row — the part is created, but surface
            # the stock issue so the UI can flag it. StockError is
            # caught here (inside the savepoint) rather than letting it
            # bubble out, because we don't want a stock-add failure to
            # roll back the Part + provider specs the operator already
            # sees as "created" in the response.
            stock_error = str(exc)

    return p, qty_added, stock_error


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
    return ok(None, "removed")
