"""Public, token-gated, read-only catalog of published parts.

This router is intentionally NOT mounted under /api and NOT gated by
require_member_for_writes — anyone with the URL gets to read.
"""
from __future__ import annotations

import hashlib
import hmac
from html import escape

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

import datetime

from app.core.config import settings
from app.core.deps import DbSession
from app.core.ratelimit import limiter
from app.core.responses import ok
from app.domain.parts.models import Part
from app.domain.workspaces.models import Workspace, WorkspaceCatalogToken

router = APIRouter()


# Same-origin headers for the public catalog responses (SEC2-009). The
# nginx layer already attaches these for the SPA bundle, but the catalog
# is fetched directly from FastAPI in some flows (e.g. the JSON endpoint
# proxied through Apache without going through nginx) so we attach them
# here too. Cheap and idempotent; nginx duplicates collapse.
_CATALOG_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'none'; "
        "frame-ancestors 'none'"
    ),
    "Permissions-Policy": "()",
}


def _token_key(request: Request) -> str:
    """Rate-limit key function: bucket per token prefix (first 16 chars).

    Using a token prefix rather than the full token limits the bucket-key
    space to a predictable size while still isolating one caller's burst
    from another's.  Falls back to the IP when no token path param is
    present (e.g. probes).
    """
    token = request.path_params.get("token", "")
    if token:
        return f"catalog:{token[:16]}"
    # Fallback: use IP so unauthenticated probes still have some cap.
    from slowapi.util import get_remote_address
    return get_remote_address(request)


def _hmac_token(token: str) -> str:
    """Compute HMAC-SHA256 of *token* keyed by SESSION_SECRET."""
    secret = settings().SESSION_SECRET
    return hmac.new(
        secret.encode(),
        token.encode(),
        hashlib.sha256,
    ).hexdigest()


def _resolve_workspace(db, token: str, request: Request | None = None) -> Workspace:
    """Constant-time catalog token lookup: hash first, then compare by hash.

    SEC2-008: the plaintext token never appears in a SQL WHERE clause.
    Instead we hash the candidate and look up by the stored hash, so the
    DB index reveals nothing about timing differences between valid and
    invalid tokens.  catalog_enabled is checked in the same query so
    a disabled workspace is indistinguishable from a wrong token (no
    enabled/disabled oracle).

    SEC2-019: lookup is performed exclusively against the
    workspace_catalog_tokens child table, which carries the
    `revoked_at IS NULL` predicate (per-recipient tokens with individual
    revocation). Migration 0032 backfills one row per workspace that had
    a legacy `Workspace.catalog_token_hash`, so existing tokens keep
    working through this single code path.

    The legacy `Workspace.catalog_token_hash` column is intentionally NOT
    consulted here: it has no `revoked_at` column, so a fallback to it
    would let rotated/revoked tokens authenticate. The column is retained
    only for rollback safety; it must never be a live auth source.
    """
    if not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="catalog not found")
    digest = _hmac_token(token)

    # --- Child table lookup (SEC2-019) — sole source of truth ---
    child = db.execute(
        select(WorkspaceCatalogToken).where(
            WorkspaceCatalogToken.token_hmac == digest,
            WorkspaceCatalogToken.revoked_at.is_(None),
        )
    ).scalar_one_or_none()

    if child is not None:
        ws = db.get(Workspace, child.workspace_id)
        if ws and ws.catalog_enabled:
            # Update last_used telemetry (best-effort, non-transactional).
            now = datetime.datetime.now(datetime.timezone.utc)
            child.last_used_at = now
            if request is not None:
                from slowapi.util import get_remote_address
                child.last_used_ip = get_remote_address(request)
            try:
                db.flush()
            except Exception:
                pass
            return ws

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="catalog not found")


def _published_parts(db, workspace_id) -> list[Part]:
    return list(
        db.execute(
            select(Part)
            .where(Part.workspace_id == workspace_id)
            .where(Part.archived_at.is_(None))
            .where(Part.published.is_(True))
            .order_by(Part.name)
        ).scalars()
    )


def _part_dict(p: Part) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "manufacturer": p.manufacturer,
        "mpn": p.mpn,
        "footprint": p.footprint,
        "description": p.description,
    }


# Tiny inline CSS keeps the page self-contained — no template engine, no static
# pipeline, no JS. The page is a leaf endpoint that needs to render once.
_PAGE_CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #f7f7f8;
  color: #18181b;
  margin: 0;
  padding: 2rem 1.25rem;
}
.wrap { max-width: 960px; margin: 0 auto; }
header { border-bottom: 1px solid #e4e4e7; padding-bottom: 1rem; margin-bottom: 1.25rem; }
.brand {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.85rem;
  color: #71717a;
  letter-spacing: 0.02em;
}
h1 { font-size: 1.5rem; margin: 0.25rem 0 0.5rem; }
.lead { color: #52525b; font-size: 0.95rem; margin: 0; }
table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e4e4e7; border-radius: 6px; overflow: hidden; }
th, td { text-align: left; padding: 0.6rem 0.8rem; font-size: 0.9rem; vertical-align: top; }
th { background: #fafafa; font-weight: 600; color: #3f3f46; border-bottom: 1px solid #e4e4e7; }
tr + tr td { border-top: 1px solid #f1f1f3; }
td.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; color: #3f3f46; }
.empty { color: #71717a; font-style: italic; padding: 1.5rem; text-align: center; background: #fff; border: 1px dashed #d4d4d8; border-radius: 6px; }
footer { color: #a1a1aa; font-size: 0.8rem; margin-top: 1.5rem; }
"""


def _render_html(ws: Workspace, parts: list[Part]) -> str:
    name = escape(ws.name)
    brand = f"stockmgr · {name} catalog"
    rows = []
    for p in parts:
        rows.append(
            "<tr>"
            f"<td>{escape(p.name or '')}</td>"
            f"<td>{escape(p.manufacturer or '')}</td>"
            f"<td class='mono'>{escape(p.mpn or '')}</td>"
            f"<td>{escape(p.footprint or '')}</td>"
            f"<td>{escape(p.description or '')}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>Name</th><th>Manufacturer</th><th>MPN</th><th>Footprint</th><th>Description</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    ) if rows else "<div class='empty'>No published parts yet.</div>"
    return (
        "<!doctype html>"
        "<html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'>"
        f"<title>{brand}</title>"
        f"<style>{_PAGE_CSS}</style>"
        "</head><body><div class='wrap'>"
        "<header>"
        f"<div class='brand'>{escape(brand)}</div>"
        f"<h1>{name}</h1>"
        "<p class='lead'>Public read-only catalog of parts this workspace has chosen to publish.</p>"
        "</header>"
        f"{table}"
        "<footer>Generated by stockmgr · This page is publicly accessible to anyone with the link.</footer>"
        "</div></body></html>"
    )


@router.get("/{token}", response_class=HTMLResponse)
@limiter.limit("60/minute", key_func=_token_key)
@limiter.limit("120/minute")  # parallel IP cap — defence in depth
def catalog_html(request: Request, token: str, db: DbSession):
    ws = _resolve_workspace(db, token, request=request)
    parts = _published_parts(db, ws.id)
    return HTMLResponse(
        content=_render_html(ws, parts),
        status_code=200,
        headers=_CATALOG_HEADERS,
    )


@router.get("/{token}/parts.json")
@limiter.limit("60/minute", key_func=_token_key)
@limiter.limit("120/minute")  # parallel IP cap — defence in depth
def catalog_json(request: Request, token: str, db: DbSession):
    ws = _resolve_workspace(db, token, request=request)
    parts = _published_parts(db, ws.id)
    body = ok(
        {
            "workspace": {"id": str(ws.id), "name": ws.name},
            "parts": [_part_dict(p) for p in parts],
        }
    )
    return JSONResponse(content=body, headers=_CATALOG_HEADERS)
