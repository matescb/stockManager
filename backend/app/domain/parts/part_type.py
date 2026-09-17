"""`parts.part_type` is derived from the provider link — keep it that way.

`part_type` was written once, at creation, and never again. Three
creation paths set it (`services/provider_import.py` → `linked`,
`projects/bom_import.py` → `local`, the REST create route → whatever the
payload asked for), and nothing maintained it afterwards. Meanwhile
`linked_provider` moves on its own: the refresh route sets it when a
primary lookup hits, and the unlink PATCH clears it. The two drifted
apart, and since the UI pill renders the raw column, provider-backed
parts told the operator they were manual ones.

So `linked` vs `local` is not an independent fact — it is a reading of
`linked_provider`, and this module is where that reading happens. Both
transitions call `sync_part_type_and_log`, which also writes the audit
row, so a route never re-derives the rule or the comment format.

Known gap, left open on purpose: the REST create route still writes
`payload.part_type` unreconciled, so an operator who picks the create
form's default `linked` and skips the MPN lookup persists `linked` with
no provider. That is the same drift in the other direction, and neither
this module nor migration 0080 corrects it — fixing it means changing
what create does, which is a product decision, not a bug fix. The pill
renders such a part as a bare `linked` with no provider name.

`meta` and `sub_assembly` are the exception, and deliberately so: they
are roles the user declared (an interchangeable group, a thing you
build), not statements about where the data came from. A meta-part with
an MPN can be refreshed from a provider — that describes its metadata,
not what it is — so a link event must leave those two alone. See
`docs/domain/parts.md` for the table of all four values.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.audit.service import log_ids as audit_log_ids
from app.domain.parts.models import Part
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace

#: The two values this module owns. Everything else is user-declared.
DERIVED_PART_TYPES = ("linked", "local")


def sync_part_type_with_link(part: Part) -> bool:
    """Re-derive `part.part_type` from `part.linked_provider`.

    Mutates `part` in place (it is an ORM row mid-transaction, so the
    caller's session already owns it) and returns whether the value
    actually changed — callers use that to decide whether there is
    anything to audit.
    """
    if part.part_type not in DERIVED_PART_TYPES:
        return False

    desired = "linked" if (part.linked_provider or "").strip() else "local"
    if part.part_type == desired:
        return False

    part.part_type = desired
    return True


def sync_part_type_and_log(
    db: Session,
    *,
    ws: Workspace,
    user: User | None,
    part: Part,
    request_id: str | None = None,
) -> bool:
    """`sync_part_type_with_link` plus the audit row, in one call.

    Returns whether the type changed. Nothing is written when it didn't:
    a re-refresh of an already-linked part is not a change and must not
    leave a trail saying it was.

    The action is `part.type_synced`, not `part.updated`, because this is
    a *derived* change the server made — not the edit the user asked for.
    The unlink PATCH writes its own `part.updated` in the same
    transaction, and collapsing the two would leave one request with two
    rows under one action, which every "latest row for this action"
    reader in the codebase assumes cannot happen.

    The comment names the field and its transition and nothing else —
    both values are fixed vocabulary, so no user or provider data can
    reach `audit_log.comment` through here.
    """
    return sync_part_type_and_log_ids(
        db,
        ws_id=ws.id,
        user_id=user.id if user else None,
        part=part,
        request_id=request_id,
    )


def sync_part_type_and_log_ids(
    db: Session,
    *,
    ws_id: UUID,
    user_id: UUID | None,
    part: Part,
    request_id: str | None = None,
) -> bool:
    """`sync_part_type_and_log` for a caller that holds ids, not ORM rows.

    Same row, same rules. The split exists for the same reason
    `audit/service.py` has `log` and `log_ids`: the provider refresh is
    now a domain service (`services/provider_refresh.py`) called from a
    route AND from the `provider-refresh` job, and the job has no `User`
    to thread through for the sake of reading `.id` off it.
    """
    if part.workspace_id != ws_id:
        # Both route callers hand over a part already scoped by
        # `_get_part`, and the job scopes its own query. This is the
        # guard for the next one: the audit row would otherwise be filed
        # under a workspace that doesn't own the part.
        raise ValueError("part_type sync: part does not belong to this workspace")

    previous = part.part_type
    if not sync_part_type_with_link(part):
        return False

    audit_log_ids(
        db,
        workspace_id=ws_id,
        user_id=user_id,
        action="part.type_synced",
        target_type="part",
        target_ids=[part.id],
        comment=f"part_type: {previous}→{part.part_type}",
        request_id=request_id,
    )
    return True
