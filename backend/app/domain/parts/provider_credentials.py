"""Resolving and storing SECONDARY parts-provider credentials.

A workspace has one PRIMARY provider (`workspaces.parts_provider`, with
its credentials in the legacy `parts_provider_api_key` / `_api_secret`
columns) and any number of SECONDARY ones, whose credentials live in
`workspace_provider_credentials`.

**This module is the resolution point for SECONDARIES.** It is not a
unification of the two stores, and the primary flow does not go through
it: four call sites read and decrypt the legacy columns directly —

    api/routes/parts_refresh.py    (refresh-from-provider, primary path)
    api/routes/parts_provider.py   (lookup-mpn)
    api/routes/parts_scan.py       (bulk-import-from-scan)
    domain/projects/bom_import_provider.py

The primary fallback inside `credentials_for` is a convenience for the
one caller that needs to accept either tier behind one name (the
`?provider=` refresh), not a claim that the table backs the primary. It
does not, by design: migration 0070 deliberately backfills nothing here,
and `PUT /api/workspaces/current/provider-credentials` refuses a payload
naming the workspace's own `parts_provider`, so one provider never has
two credential stores.

Consequence worth knowing before anyone plans to retire the legacy
columns: those four call sites have to be migrated onto this module
first. Dropping the columns while they still read them breaks the whole
primary flow.

Nothing in this module ever logs, returns, or serializes plaintext
beyond the tuple it hands back to `make_provider`.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.secrets import decrypt, encrypt
from app.core.time import utcnow
from app.domain.parts.models import WorkspaceProviderCredential

__all__ = [
    "ProviderCredentials",
    "active_credential_rows",
    "clear",
    "credentials_for",
    "serialize_credential",
    "upsert",
]

# (api_key, api_secret) in plaintext. api_secret is None for providers
# that need only one credential (Mouser).
ProviderCredentials = tuple[str | None, str | None]


def _active_row(
    db: Session, workspace_id: UUID, provider: str
) -> WorkspaceProviderCredential | None:
    return db.execute(
        select(WorkspaceProviderCredential)
        .where(WorkspaceProviderCredential.workspace_id == workspace_id)
        .where(WorkspaceProviderCredential.provider == provider)
        .where(WorkspaceProviderCredential.archived_at.is_(None))
    ).scalars().first()


def active_credential_rows(
    db: Session, workspace_id: UUID
) -> list[WorkspaceProviderCredential]:
    """Every live credentials row for a workspace, provider-ordered."""
    return list(
        db.execute(
            select(WorkspaceProviderCredential)
            .where(WorkspaceProviderCredential.workspace_id == workspace_id)
            .where(WorkspaceProviderCredential.archived_at.is_(None))
            .order_by(WorkspaceProviderCredential.provider)
        ).scalars()
    )


def credentials_for(db: Session, ws, provider: str) -> ProviderCredentials | None:
    """Plaintext credentials for *provider* in *ws*, or None if unset.

    Resolution order:
      1. the workspace's active `workspace_provider_credentials` row —
         secondaries only, and the case this function exists for;
      2. for the workspace's PRIMARY provider only, the legacy
         `workspaces.parts_provider_api_*` columns.

    Step (2) is a convenience so `?provider=` can name either tier; the
    primary's own flows read those columns directly (see the module
    docstring). The two branches are mutually exclusive in practice — the
    PUT route refuses to store the primary — so this never has to choose
    between two live keys for one provider.
    """
    row = _active_row(db, ws.id, provider)
    if row is not None and row.api_key_encrypted:
        return (decrypt(row.api_key_encrypted), decrypt(row.api_secret_encrypted))

    if provider == (ws.parts_provider or None) and ws.parts_provider_api_key:
        return (
            decrypt(ws.parts_provider_api_key),
            decrypt(ws.parts_provider_api_secret),
        )
    return None


def upsert(
    db: Session,
    *,
    ws,
    user_id: UUID | None,
    provider: str,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> WorkspaceProviderCredential | None:
    """Store credentials for *provider*, returning the live row.

    `None` for a field leaves whatever is stored alone; the empty string
    clears it — the same idiom as the workspace PATCH credential fields.
    When both fields end up empty the row is archived and `None` is
    returned, so "clear everything" and "never configured" converge on
    one state.

    Caller owns the transaction (`get_db` commits).
    """
    row = _active_row(db, ws.id, provider)
    if row is None:
        # Nothing to clear, and no point minting a row that would be
        # archived on the same call.
        if not (api_key or api_secret):
            return None
        row = WorkspaceProviderCredential(
            workspace_id=ws.id,
            provider=provider,
            created_by=user_id,
        )
        db.add(row)

    if api_key is not None:
        row.api_key_encrypted = encrypt(api_key)
    if api_secret is not None:
        row.api_secret_encrypted = encrypt(api_secret)
    row.updated_by = user_id

    if not row.api_key_encrypted and not row.api_secret_encrypted:
        row.archived_at = utcnow()
        db.flush()
        return None

    db.flush()
    return row


def clear(db: Session, *, ws, user_id: UUID | None, provider: str) -> bool:
    """Archive the credentials row for *provider*. True if one was live."""
    row = _active_row(db, ws.id, provider)
    if row is None:
        return False
    row.archived_at = utcnow()
    row.updated_by = user_id
    db.flush()
    return True


def serialize_credential(row: WorkspaceProviderCredential) -> dict:
    """API shape — presence flags only, never the values or ciphertext."""
    return {
        "provider": row.provider,
        "has_api_key": bool(row.api_key_encrypted),
        "has_api_secret": bool(row.api_secret_encrypted),
    }
