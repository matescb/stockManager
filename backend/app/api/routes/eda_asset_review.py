"""Per-file metadata and review for a part's CAD files (SM-010).

Mounted under `/api/parts` beside `eda.parts_router`:

* `GET  /{part_id}/eda/assets` — `{assets: {kind: entry}, can_review}`,
  the same `assets` shape `get_part_eda` returns over MCP, plus whether
  THIS caller may record a review (the CAD tab shows the control on it).
* `PUT  /{part_id}/eda/{kind}/review` — record reviewed / rejected /
  unreviewed on the file's current version.

"Owner" for a review means a workspace `owner` or `admin` signed in with
a browser SESSION. Any API token — however privileged its owner — is
refused, and so is a member or viewer session, all with the one code
`eda.review_requires_owner`: a review is a person's statement that they
checked a file against a datasheet, and a token in an agent's config
cannot make it. The descriptive metadata has no REST writer; it is the
MCP `set_asset_metadata` tool's job.

Lives in its own module because `eda.py` is past the size ceiling.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes._eda_shared import audit as _audit
from app.api.routes._parts_shared import get_part as _get_part
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, membership_role
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import Envelope, ok
from app.domain.eda import asset_metadata
from app.domain.eda import service as eda_service

parts_router = APIRouter()

_REVIEW_RATE = "60/minute"
_REVIEW_ROLES = frozenset({"owner", "admin"})

AssetKind = Literal["symbol", "footprint", "model3d", "spice"]


class AssetReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["unreviewed", "reviewed", "rejected"]
    # What the file was checked against, e.g. "datasheet rev C fig 12".
    against: str | None = Field(default=None, max_length=500)
    # The version the reviewer looked at; refused with eda.asset_changed
    # if the file was replaced since.
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


def _may_review(request: Request, db, user, ws) -> bool:
    if getattr(request.state, "api_token", None) is not None:
        return False
    return membership_role(db, user, ws) in _REVIEW_ROLES


def require_review_session(
    request: Request, user: CurrentUser, ws: CurrentWorkspace, db: DbSession
) -> None:
    """Dependency: an admin/owner browser session, never an API token.

    Takes `user` so the token authentication has run (and set
    `request.state.api_token`) before this reads it — the same ordering
    reason `deps.forbid_api_token` gives.
    """
    if not _may_review(request, db, user, ws):
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.EDA_REVIEW_REQUIRES_OWNER,
            "a review is recorded by a workspace owner or admin signed in to "
            "the app; API tokens and other roles cannot change it",
        )


@parts_router.get("/{part_id}/eda/assets")
def get_part_eda_assets(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[dict]:
    part = _get_part(db, ws.id, part_id, include_archived=True)
    config = eda_service.get_part_eda(db, ws=ws, part=part)
    return ok(
        {
            "assets": asset_metadata.assets_payload(
                db, ws=ws, part_id=part.id, config=config
            ),
            "can_review": _may_review(request, db, user, ws),
        }
    )


@parts_router.put(
    "/{part_id}/eda/{kind}/review",
    dependencies=[Depends(require_review_session)],
)
@limiter.limit(_REVIEW_RATE, key_func=workspace_key)
def put_part_eda_asset_review(
    request: Request,
    part_id: UUID,
    kind: AssetKind,
    payload: AssetReviewIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[dict]:
    part = _get_part(db, ws.id, part_id)
    ref = asset_metadata.set_review(
        db,
        ws=ws,
        part=part,
        kind=kind,
        user_id=user.id,
        state=payload.state,
        against=payload.against,
        expected_sha256=payload.sha256,
    )
    # Field names and the verdict only — `against` is free text.
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_asset_review.set",
        target_type="part_eda",
        target_id=part.id,
        comment=f"kind={kind},sha256={ref.row.sha256},state={payload.state}",
    )
    return ok(
        {
            "kind": kind,
            "asset": asset_metadata.asset_payload(db, ws=ws, part=part, kind=kind),
        }
    )
