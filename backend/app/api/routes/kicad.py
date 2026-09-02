"""KiCad HTTP library — `/kicad-api/v1`, spoken to KiCad's dialect.

Three things make this router unlike every other one in the app, all of
them forced by the client:

* **Not the envelope.** KiCad parses fixed JSON documents. `{data,
  status}` around them would be unparseable, so these routes return the
  raw shapes `domain/eda/kicad_library.py` builds. That is also why the
  router is mounted at `/kicad-api` rather than under `/api`, the same
  way the public catalog sits at `/catalog`.

* **Header auth only, and 404 for everything it can distinguish.**
  KiCad sends `Authorization: Token <pat>` and accepts nothing but HTTP
  200 — every other status is "library unavailable" to it. So there is
  no reason to tell a missing token from a revoked one from an unknown
  part, and all of those collapse to one 404 (mirroring `catalog.py`).
  The single exception is the rate limiter's 429, which is raised by
  slowapi before any of this router's code runs and is deliberately
  left distinct: it is not an oracle (it is reached without a valid
  credential) and flattening it would cost the caller the
  `Retry-After` header. Session cookies are never accepted: one
  surface, one credential.

* **Read-only tokens welcome.** Everything here is a GET, so a token
  minted `read_only` — the one you paste into a config file on a
  workstation — is exactly the right credential.

Thin routes: the queries, the eligibility rule and the JSON shaping all
live in `app/domain/eda/kicad_library.py`; the naming contract those
documents carry lives in `app/domain/eda/kicad_refs.py` and is shared
with the phase-6 library generation.
"""
from __future__ import annotations

import hashlib
from typing import Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.deps import DbSession, try_authenticate_api_token
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter
from app.domain.eda import kicad_library
from app.domain.workspaces.models import Workspace

router = APIRouter()

# Where `main.py` mounts this router. Re-exported from the domain module
# so the path the app answers on and the `root_url` it advertises in
# `GET /api/eda/kicad-setup` are one value that cannot drift.
API_PREFIX = kicad_library.API_PREFIX

# The symbol chooser opens with a burst — one categories fetch and a
# parts fetch per category the user expands — and the client caches its
# results, so a steady-state workstation is far under this.
#
# BOTH buckets are checked on EVERY request, valid credential or not:
# `_token_key` reads the raw header rather than anything auth produced,
# and the routes resolve the token in their body so slowapi's wrapper
# runs first (see `kicad_workspace`). A credential-stuffing flood
# therefore hits the IP cap; rotating the token to dodge the token
# bucket does not dodge that one.
_TOKEN_RATE = "120/minute"
_IP_RATE = "240/minute"

# Not the app envelope (see the module docstring), and not sniffable
# either — these are JSON documents fetched by a client that will
# happily follow whatever the body says.
_HEADERS = {"X-Content-Type-Options": "nosniff"}


def _not_found() -> NoReturn:
    """The one and only failure on this surface."""
    raise_http(status.HTTP_404_NOT_FOUND, ErrorCodes.KICAD_NOT_FOUND, "not found")


def _token_key(request: Request) -> str:
    """Rate-limit bucket: a digest prefix of the presented token.

    Hashed rather than sliced (which is what `catalog.py::_token_key`
    does with its URL-path token) because bucket keys end up in memory
    dumps and, if slowapi ever grows a backend, in a store we don't
    control. A digest isolates one caller's burst from another's without
    the key itself being a credential fragment.
    """
    _, _, raw = (request.headers.get("Authorization") or "").partition(" ")
    raw = raw.strip()
    if not raw:
        # No credential to bucket on — fall back to the IP so a flood of
        # anonymous probes still hits a limit.
        return get_remote_address(request)
    return "kicad:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def kicad_workspace(request: Request, db: Session) -> Workspace:
    """The workspace a valid PAT pins this request to, else 404.

    Called as the FIRST LINE OF EACH ROUTE BODY, not as a FastAPI
    dependency, and that is load-bearing. slowapi checks its buckets
    inside the wrapper around the endpoint function, which runs *after*
    FastAPI has resolved every dependency — so as a dependency this
    would 404 an invalid token before the limiter ever saw the request,
    and a credential-stuffing flood would cost nothing. `catalog.py`
    resolves its token in the body for the same reason.

    Sets `request.state.workspace_id` so downstream telemetry sees a
    verified tenant.
    """
    if try_authenticate_api_token(request, db) is None:
        _not_found()

    token = request.state.api_token
    ws = db.get(Workspace, token.workspace_id)
    if ws is None:
        _not_found()
    request.state.workspace_id = str(ws.id)
    return ws


def _json(payload: Any) -> JSONResponse:
    return JSONResponse(content=payload, headers=_HEADERS)


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _uuid_or_404(value: str) -> UUID:
    """Parse a path id. A malformed one is indistinguishable from an
    unknown one — 404 either way, never a 422 envelope."""
    parsed = _parse_uuid(value)
    if parsed is None:
        _not_found()
    return parsed


@router.get("/v1/")
@limiter.limit(_TOKEN_RATE, key_func=_token_key)
@limiter.limit(_IP_RATE)
def kicad_root(request: Request, db: DbSession) -> JSONResponse:
    """The endpoint map KiCad probes when the library is first opened."""
    kicad_workspace(request, db)
    return _json(kicad_library.root_document())


@router.get("/v1/categories.json")
@limiter.limit(_TOKEN_RATE, key_func=_token_key)
@limiter.limit(_IP_RATE)
def kicad_categories(request: Request, db: DbSession) -> JSONResponse:
    ws = kicad_workspace(request, db)
    return _json(kicad_library.list_categories(db, workspace_id=ws.id))


@router.get("/v1/parts/category/{category_id}.json")
@limiter.limit(_TOKEN_RATE, key_func=_token_key)
@limiter.limit(_IP_RATE)
def kicad_parts_in_category(
    request: Request,
    category_id: str,
    db: DbSession,
) -> JSONResponse:
    """Eligible parts in one category.

    `category_id` is a category UUID or the literal `uncategorized` —
    the synthetic bucket `categories.json` appends for parts that have
    no category.
    """
    ws = kicad_workspace(request, db)
    if category_id == kicad_library.UNCATEGORIZED_ID:
        return _json(kicad_library.list_parts(db, workspace_id=ws.id, category_id=None))

    resolved = _uuid_or_404(category_id)
    if not kicad_library.category_exists(db, workspace_id=ws.id, category_id=resolved):
        _not_found()
    return _json(kicad_library.list_parts(db, workspace_id=ws.id, category_id=resolved))


@router.get("/v1/parts/{part_id}.json")
@limiter.limit(_TOKEN_RATE, key_func=_token_key)
@limiter.limit(_IP_RATE)
def kicad_part(
    request: Request,
    part_id: str,
    db: DbSession,
) -> JSONResponse:
    """One part's full symbol definition.

    404 covers "no such part", "another workspace's part", "archived"
    and "has no symbol" alike — the last of those is why the listings
    and this route have to agree on eligibility.
    """
    ws = kicad_workspace(request, db)
    document = kicad_library.part_detail(
        db, workspace_id=ws.id, part_id=_uuid_or_404(part_id)
    )
    if document is None:
        _not_found()
    return _json(document)
