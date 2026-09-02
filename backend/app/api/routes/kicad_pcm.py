"""PCM repository — `/kicad-api/pcm/{token}/…`, for KiCad's add-on manager.

A second dialect on the phase-5 mount. Where `/kicad-api/v1` answers the
symbol chooser's live queries, this serves the Plugin and Content
Manager: paste one URL into Preferences → Plugin and Content Manager →
Manage, and the workspace's symbol, footprint and 3D libraries install as
a package that KiCad registers in `sym-lib-table` and `fp-lib-table` by
itself. `domain/eda/pcm.py` builds everything served here.

The token is in the URL, and that is not a choice
-------------------------------------------------

The PCM issues plain GETs with no `Authorization` header — not for the
repository, not for `packages.json`, not for the archive. A private
repository therefore has to carry its credential in the path, where it
also lands in proxy access logs, in `~/.config/kicad/*/kicad_common.json`
on the workstation, and anywhere the user pastes the URL.

Three things bound that exposure:

* **Read-only tokens only.** A full-parity token in a URL is precisely
  the leak ADR-0029 minted the `read_only` flag for. One presented here
  is refused with the same 404 as a revoked one — no hint that the
  credential was real, and no reason for a user to try it twice.
* **The app's own error log masks the segment**
  (`core/responses.py::mask_credential_segment`), so a flood of bad-token probes
  doesn't write live credentials into the journal.
* **`Cache-Control: no-store` and `Referrer-Policy: no-referrer`** on
  every response, so neither an intermediary nor a browser that wandered
  onto the URL holds a copy.

Everything else mirrors `kicad.py`: rejections are indistinguishable
404s, the token is resolved in the ROUTE BODY so slowapi's wrapper runs
before authentication (a credential-stuffing flood must cost the
attacker its rate limit), and rate-limit buckets key on a digest of the
token rather than the token.
"""
from __future__ import annotations

import hashlib
from typing import NoReturn

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import FileResponse
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.deps import DbSession, try_authenticate_url_token
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter
from app.domain.eda import pcm
from app.domain.workspaces.models import Workspace

router = APIRouter()

# The PCM checks for updates on startup and when the dialog is opened —
# a handful of requests per session, not a stream. The JSON documents are
# cheap; the archive is a build, so it gets a tighter cap of its own.
_TOKEN_RATE = "60/minute"
_IP_RATE = "120/minute"
_ARCHIVE_TOKEN_RATE = "10/minute"
_ARCHIVE_IP_RATE = "30/minute"

# `no-store` because the URL is a credential and these bodies name it in
# turn; `noindex` because a pasted-into-a-wiki repository URL should not
# become a crawlable one. The PCM does its own version comparison and
# never relies on HTTP caching, so nothing is lost.
_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}

_JSON_MEDIA_TYPE = "application/json"
_ZIP_MEDIA_TYPE = "application/zip"


def _not_found() -> NoReturn:
    """The one failure this surface distinguishes — which is to say none.

    Bad token, revoked token, expired token, a full-parity token used
    where only a read-only one is accepted: all the same 404, for the
    same reason `kicad.py` collapses its failures.
    """
    raise_http(status.HTTP_404_NOT_FOUND, ErrorCodes.KICAD_NOT_FOUND, "not found")


def _token_key(request: Request) -> str:
    """Rate-limit bucket: a digest prefix of the token in the path.

    Hashed, never sliced. `catalog.py` slices its URL token, but bucket
    keys outlive the request in slowapi's store and would outlive it in a
    shared backend — a prefix of a live credential is not something to
    leave lying there.
    """
    raw = request.path_params.get("token") or ""
    if not raw:
        return get_remote_address(request)
    return "pcm:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def pcm_workspace(request: Request, db: Session, token: str) -> Workspace:
    """The workspace a valid READ-ONLY token pins this request to, else 404.

    Called as the first line of each route body rather than as a
    dependency — see `kicad.py::kicad_workspace` for why that ordering is
    load-bearing against the rate limiter.

    The read-only check deliberately comes after authentication, so the
    attempt is recorded in `last_used_at` before it is refused: someone
    probing this surface with a stolen full-parity token is exactly what
    that column exists to make visible.
    """
    if try_authenticate_url_token(request, db, token) is None:
        _not_found()

    row = request.state.api_token
    if not row.read_only:
        _not_found()

    ws = db.get(Workspace, row.workspace_id)
    if ws is None:
        _not_found()
    request.state.workspace_id = str(ws.id)
    return ws


def _json(payload: bytes) -> Response:
    return Response(content=payload, media_type=_JSON_MEDIA_TYPE, headers=_HEADERS)


def _build(request: Request, db: Session, token: str):
    """Resolve the caller and materialise their package.

    Every route here is a plain `def`, which FastAPI runs in its
    threadpool — so the file reads and the deflate never touch the event
    loop, and neither do the synchronous queries `plan_package` issues.
    An `async def` route would have had to hand the build off explicitly
    and would still have run those queries on the loop.

    The build is bounded rather than streamed: `pcm.MAX_CONTENT_BYTES` is
    checked against the database before a single file is opened, so an
    outsized workspace is refused in milliseconds instead of occupying a
    worker until nginx's 60-second read timeout fires.
    """
    ws = pcm_workspace(request, db, token)
    return pcm.materialise(pcm.plan_package(db, ws=ws))


@router.get(f"/pcm/{{token}}/{pcm.REPOSITORY_DOCUMENT}")
@limiter.limit(_TOKEN_RATE, key_func=_token_key)
@limiter.limit(_IP_RATE)
def pcm_repository(request: Request, token: str, db: DbSession) -> Response:
    """The document a user pastes into the PCM's repository list.

    It publishes the SHA-256 of `packages.json`, which the PCM verifies —
    so that document has to be built here too, byte for byte as the next
    request will serve it.
    """
    built = _build(request, db, token)
    packages = pcm.json_bytes(
        pcm.packages_document(
            built, download_url=pcm.document_url(token, pcm.PACKAGE_ARCHIVE)
        )
    )
    return _json(
        pcm.json_bytes(
            pcm.repository_document(
                built.plan,
                packages_url=pcm.document_url(token, pcm.PACKAGES_DOCUMENT),
                packages_sha256=hashlib.sha256(packages).hexdigest(),
            )
        )
    )


@router.get(f"/pcm/{{token}}/{pcm.PACKAGES_DOCUMENT}")
@limiter.limit(_TOKEN_RATE, key_func=_token_key)
@limiter.limit(_IP_RATE)
def pcm_packages(request: Request, token: str, db: DbSession) -> Response:
    """The package list, with the archive's size and digest.

    A workspace holding no library content answers `{"packages": []}` —
    a valid repository with nothing to install, rather than a package
    that installs to nothing.
    """
    built = _build(request, db, token)
    return _json(
        pcm.json_bytes(
            pcm.packages_document(
                built, download_url=pcm.document_url(token, pcm.PACKAGE_ARCHIVE)
            )
        )
    )


@router.get(f"/pcm/{{token}}/{pcm.PACKAGE_ARCHIVE}")
@limiter.limit(_ARCHIVE_TOKEN_RATE, key_func=_token_key)
@limiter.limit(_ARCHIVE_IP_RATE)
def pcm_package_archive(
    request: Request, token: str, db: DbSession
) -> Response:
    """The package itself.

    Served even for an empty workspace, where it is a zip holding only
    `metadata.json`: `packages.json` never offers it, so nothing reaches
    this by following the documents, and a 404 here would be a state
    oracle that told a caller whether the workspace had any content.
    """
    built = _build(request, db, token)
    headers = {
        **_HEADERS,
        # No `filename*`/UTF-8 form needed: every character here is from
        # the identifier, which is hex and dots.
        "Content-Disposition": (
            f'attachment; filename="{built.identifier}-{built.version}.zip"'
        ),
    }
    if built.serve_from_disk:
        # Streamed off disk rather than read into the worker. The cap on
        # package content is 200 MiB and this route allows 30 requests a
        # minute per address, so buffering the body would put that
        # multiple in resident memory for no benefit — nothing here
        # inspects the bytes.
        return FileResponse(built.path, media_type=_ZIP_MEDIA_TYPE, headers=headers)
    return Response(content=built.data, media_type=_ZIP_MEDIA_TYPE, headers=headers)
