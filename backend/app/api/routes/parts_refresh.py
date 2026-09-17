"""Provider refresh and secondary-link teardown.

POST   /{part_id}/refresh-from-provider     — re-run MPN lookup, reconcile custom fields
DELETE /{part_id}/provider-links/{provider} — drop a secondary provider's link + fields

Split out of `parts_assets.py` (CQ-002 line-count budget), which is back
to serving content-addressed assets only. Mounted under the same
/api/parts prefix in main.py, so no URL changed.

A workspace has ONE primary provider and any number of secondaries, and
each reconciles strictly inside its own custom-field namespace. See
ADR-0031 and `domain/parts/provider_fields.py`.

The refresh sequence itself lives in
`domain/parts/services/provider_refresh.py`: the `provider-refresh`
operator job runs the same one over a whole workspace, and two copies of
it would be two answers to the questions ADR-0031 and ADR-0034 settled
once. What is left here is HTTP — the status codes, the envelope, the
rate limit and the `missing_specs` badge.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import select

from app.api.routes._parts_shared import (
    get_part as _get_part,
)
from app.api.routes._parts_shared import (
    missing_specs_for_parts as _missing_specs_for_parts,
)
from app.api.routes._parts_shared import (
    serialize_part as _serialize,
)
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.domain.audit.service import log as _audit_log
from app.domain.categories.service import category_index
from app.domain.custom_fields.models import CustomField
from app.domain.parts.provider_fields import provider_wrote_custom_field_row
from app.domain.parts.provider_links import (
    delete_link,
    get_link,
    links_for_part,
    serialize_link,
)
from app.domain.parts.providers.base import ProviderUpstreamError
from app.domain.parts.services.provider_refresh import (
    MissingMpnError,
    ProviderNotConfiguredError,
    UnknownProviderError,
    ensure_refreshable,
    refresh_part,
)
from app.domain.stock.service import reserved_quantity, total_for_part

router = APIRouter()


@router.post("/{part_id}/refresh-from-provider")
@limiter.limit("60/minute", key_func=workspace_key)
def refresh_from_provider(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    provider: str | None = Query(default=None, max_length=40),
):
    """Re-run an MPN lookup against this part's stored MPN.

    `?provider=` selects which configured provider to refresh from.
    Omitted — or naming the workspace's own `parts_provider` — runs the
    PRIMARY flow: it owns the part columns (manufacturer / mpn /
    footprint, and description unless locally edited), `parts.linked_*`,
    and the un-namespaced `source='provider'` custom fields.

    Any other known provider runs as a SECONDARY: it writes no part
    column at all, only a `part_provider_links` row and custom fields
    under its own `"{provider}:"` prefix. Both tiers reconcile strictly
    inside their own namespace, so refreshing one never disturbs the
    other's rows.
    """
    p = _get_part(db, ws.id, part_id)

    try:
        # The MPN precondition first: the snapshot below is a full
        # `part_categories` read, and paying for it only to answer 400 is
        # work nobody asked for. The service checks it again, so there is
        # still one definition of the rule.
        ensure_refreshable(p)
        # One snapshot of the workspace's tree for the whole request. Both
        # readers need it — `apply_provider_category` inside the service
        # asks it three questions, and the `missing_specs` badge on the
        # response asks it a fourth — and building it per caller made a
        # plain refresh scan `part_categories` three times over. Nothing
        # in the refresh creates a category, so the snapshot cannot go
        # stale under itself.
        categories = category_index(db, ws_id=ws.id)
        outcome = refresh_part(
            db,
            ws=ws,
            part=p,
            provider_name=provider,
            user_id=user.id,
            request_id=getattr(request.state, "request_id", None),
            category_index=categories,
        )
    except MissingMpnError as exc:
        raise_http(
            400,
            code=ErrorCodes.PART_PROVIDER_MISSING_MPN,
            message=exc.message,
        )
    except UnknownProviderError as exc:
        raise_http(
            422,
            code=ErrorCodes.PART_PROVIDER_UNKNOWN,
            message=exc.message,
            provider=exc.provider,
        )
    except ProviderNotConfiguredError as exc:
        raise_http(
            400,
            code=ErrorCodes.PART_PROVIDER_NOT_CONFIGURED,
            message=exc.message,
            **({"provider": exc.provider} if exc.provider else {}),
        )
    except ProviderUpstreamError as exc:
        raise_http(
            exc.status_code,
            code=ErrorCodes.PROVIDER_UPSTREAM_ERROR,
            message=exc.message,
            provider=exc.provider,
        )

    if not outcome.found:
        # No link row is created for a miss — a part the provider has
        # never heard of is not linked to it.
        return ok(
            {
                "found": False,
                "message": outcome.error or "no match",
                "provider": outcome.provider,
            }
        )

    return ok(
        {
            "found": True,
            "provider": outcome.provider,
            # `summary` counts what the reconcile did: `skipped` is fields
            # this payload could not be written under (a key too wide for
            # the column, or a bare key that spells a canonical one),
            # `archived` junk rows retired from the part, `restored` rows
            # brought back because upstream answered their key again, and
            # `dropped` payload keys refused outright as junk.
            "summary": outcome.report.summary(),
            # The category the provider's taxonomy named when this
            # workspace has nowhere to file the part. Null when the part
            # was filed, or when the taxonomy said nothing we recognise.
            "category_suggestion": outcome.category.suggestion,
            "link": serialize_link(outcome.link),
            "part": _serialize(
                p,
                on_hand=total_for_part(db, workspace_id=ws.id, part_id=p.id),
                reserved=reserved_quantity(db, workspace_id=ws.id, part_id=p.id),
                provider_links=[
                    serialize_link(row)
                    for row in links_for_part(db, workspace_id=ws.id, part_id=p.id)
                ],
                missing_specs=_missing_specs_for_parts(
                    db, ws.id, [p], index=categories
                ).get(p.id, []),
            ),
        }
    )


@router.delete("/{part_id}/provider-links/{provider}")
@limiter.limit("60/minute", key_func=workspace_key)
def delete_provider_link(
    request: Request,
    part_id: UUID,
    provider: str,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Unlink a SECONDARY provider from this part.

    Drops the link row, deletes the `source='provider'` fields this
    provider wrote, and demotes its `override` rows to plain `manual` —
    the user edited those, so they survive as their own.

    "Wrote" is `custom_fields.provider` plus its `"{provider}:"`
    namespace, not the namespace alone: since A3 a secondary also writes
    un-namespaced CANONICAL keys (`resistance`), and leaving those behind
    would make an unlinked provider's data permanently unattributable.
    A row another provider stamped, and an unstamped row outside this
    namespace, are both left exactly as they are.

    The primary is not unlinkable here: that is `PATCH /api/parts/{id}`
    with `unlink_provider=true`, which also releases the part columns.

    The guard reads `ws.parts_provider`, NOT `p.linked_provider`. Which
    tier a provider occupies is a workspace-level fact; `linked_provider`
    only records which provider last drove this part's columns, and it is
    sticky — it survives an admin switching the workspace primary. Keying
    the guard off it would permanently strand a link that the workspace's
    own configuration now says is a secondary, with no route able to
    remove it. Releasing the part columns stays PATCH's job either way.
    """
    p = _get_part(db, ws.id, part_id)
    name = provider.strip().lower()

    if name and name == (ws.parts_provider or "").strip().lower():
        raise_http(
            400,
            code=ErrorCodes.PART_PROVIDER_LINK_IS_PRIMARY,
            message=(
                f"'{name}' is this workspace's primary provider; "
                "PATCH the part with unlink_provider=true instead"
            ),
            provider=name,
        )

    row = get_link(db, workspace_id=ws.id, part_id=p.id, provider=name)
    if row is None:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.PART_PROVIDER_LINK_NOT_FOUND,
            message="provider link not found",
        )
    delete_link(db, row)

    field_rows = [
        cf
        for cf in db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == ws.id)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == p.id)
            .where(CustomField.source.in_(["provider", "override"]))
        ).scalars()
        if provider_wrote_custom_field_row(name, cf, is_primary=False)
    ]
    removed = 0
    for cf in field_rows:
        if cf.source == "provider":
            db.delete(cf)
            removed += 1
        else:
            cf.source = "manual"
            cf.original_value = None
            # The row is the user's now. Leaving the stamp on would let a
            # later refresh from the same provider treat it as its own.
            cf.provider = None
            cf.updated_by = user.id

    _audit_log(
        db,
        ws=ws,
        user=user,
        action="part.provider_unlinked",
        target_type="part",
        target_ids=[p.id],
        comment=f"provider={name}",
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(
        {
            "provider": name,
            "removed_fields": removed,
            "provider_links": [
                serialize_link(link)
                for link in links_for_part(db, workspace_id=ws.id, part_id=p.id)
            ],
        }
    )
