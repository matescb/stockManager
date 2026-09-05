"""Mint and resolve universal object codes.

Two operations, both workspace-scoped:

* :func:`mint_or_get` — idempotent get-or-create. Validates that the
  referenced entity lives in the caller's workspace *before* minting, so
  a foreign UUID is a 404 rather than a code pointing at another tenant's
  row.
* :func:`resolve` — the scan path. Normalises the typed/scanned string
  and looks it up within the workspace; anything else is a 404.

Writes `db.flush()`; the `get_db` dependency owns the commit.

Code format
-----------
Eight characters from `Crockford's base32
<https://www.crockford.com/base32.html>`_ alphabet — the digits plus the
uppercase letters minus ``I``, ``L``, ``O`` and ``U``. Three properties
matter here:

1. **Transcribable.** The excluded letters are exactly the ones people
   confuse with ``1`` and ``0``; ``U`` is dropped so a random draw cannot
   spell an obscenity. Someone reading a smudged label back over the
   phone has no ambiguous characters to guess at.
2. **Dense in a QR.** An all-uppercase-alphanumeric payload encodes in
   QR's alphanumeric mode rather than byte mode — roughly 45 bits per
   trio of characters instead of 8 bits per character — so the printed
   symbol stays small and scans from further away.
3. **Opaque.** The code is drawn from `secrets` (a CSPRNG), not derived
   from the row's UUID or a counter. Sequential or UUID-derived codes
   would leak object counts and let a scanner walk a workspace's ids;
   32**8 ≈ 1.1e12 possibilities with per-workspace scoping means guessing
   one is not a viable attack.

Codes are *not* secrets — a code appears on a label anyone in the
warehouse can photograph, and the resolver still requires an
authenticated session scoped to the owning workspace. The entropy buys
unguessability and collision-freedom, not confidentiality.
"""
from __future__ import annotations

import secrets
from uuid import UUID

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import assert_polymorphic_in_workspace
from app.core.errors import ErrorCodes, raise_http
from app.domain.codes.models import CODE_MAX_LENGTH, ObjectCode
from app.domain.workspaces.models import Workspace

# Crockford base32: 0-9 plus A-Z minus I, L, O, U.
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

CODE_LENGTH = 8

# Crockford's canonical decode aliases: the letters that were removed
# because they look like digits map back onto those digits, so a person
# who reads "O" off a label and types the letter still lands on the right
# row. `U` has no digit twin and is simply not a valid character.
_DECODE_ALIASES = {"I": "1", "L": "1", "O": "0"}

# Separators a human or a scanner may introduce. Hyphens are the common
# hand-written grouping; whitespace shows up when a code is copied out of
# a wrapped line.
_STRIPPED_CHARS = " \t\r\n-_"

# Collisions are astronomically unlikely (32**8 per workspace), so this
# only ever runs once in practice. It exists so a pathological workspace
# — or a future shorter code length — degrades into a retry rather than a
# 500 from the unique index.
_MAX_MINT_ATTEMPTS = 8


def generate_code(length: int = CODE_LENGTH) -> str:
    """A fresh random code. CSPRNG-backed, uniform over the alphabet."""
    return "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(length))


def normalize_code(raw: str) -> str:
    """Canonicalise a scanned or typed code for lookup.

    Upper-cases, drops grouping separators, and applies the Crockford
    decode aliases (``I``/``L`` → ``1``, ``O`` → ``0``). The result is
    what is stored, so a code that round-trips through a human is still
    found. Returns the empty string for input that normalises to nothing;
    callers treat that as "not found".
    """
    out = []
    for ch in raw.upper():
        if ch in _STRIPPED_CHARS:
            continue
        out.append(_DECODE_ALIASES.get(ch, ch))
    return "".join(out)


def _by_entity(
    db: Session, *, workspace_id: UUID, entity_type: str, entity_id: UUID
) -> ObjectCode | None:
    return db.execute(
        select(ObjectCode).where(
            ObjectCode.workspace_id == workspace_id,
            ObjectCode.entity_type == entity_type,
            ObjectCode.entity_id == entity_id,
        )
    ).scalar_one_or_none()


def mint_or_get(
    db: Session,
    *,
    ws: Workspace,
    entity_type: str,
    entity_id: UUID,
) -> tuple[ObjectCode, bool]:
    """Return this object's code, minting one on first request.

    Returns ``(row, created)`` so the caller can decide whether to write
    an audit row — re-reading an existing code is not a mutation.

    Raises 404 when ``entity_id`` names a row that does not exist in
    ``ws``. That check is load-bearing: without it a caller in workspace
    B could mint a code against workspace A's part id and then resolve
    it, turning the resolver into a cross-tenant existence oracle.
    """
    # Existence + workspace check first: never mint against a UUID we
    # have not proven belongs here.
    assert_polymorphic_in_workspace(db, entity_type, entity_id, ws.id)

    existing = _by_entity(
        db, workspace_id=ws.id, entity_type=entity_type, entity_id=entity_id
    )
    if existing is not None:
        return existing, False

    for _ in range(_MAX_MINT_ATTEMPTS):
        row = ObjectCode(
            workspace_id=ws.id,
            entity_type=entity_type,
            entity_id=entity_id,
            code=generate_code(),
        )
        try:
            # SAVEPOINT so a unique violation rolls back just this INSERT
            # and leaves the caller's transaction (and its audit row)
            # usable.
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # Two constraints can fire here. A concurrent mint for the
            # same object (uq_object_codes_ws_entity) means someone else
            # won — take their code. Otherwise it was a code collision;
            # draw again.
            existing = _by_entity(
                db, workspace_id=ws.id, entity_type=entity_type, entity_id=entity_id
            )
            if existing is not None:
                return existing, False
            continue
        return row, True

    # 409 rather than a 5xx: `core/errors.py` reserves 5xx for genuine
    # server faults, and this is a (vanishingly rare) conflict the caller
    # can resolve by retrying.
    raise_http(
        status.HTTP_409_CONFLICT,
        ErrorCodes.CODE_MINT_EXHAUSTED,
        "could not allocate a unique code; please retry",
    )


def resolve(db: Session, *, ws: Workspace, code: str) -> ObjectCode:
    """Look up the object a scanned code points at, or raise 404.

    Scoped to ``ws`` — a code minted in another workspace is
    indistinguishable from one that was never minted at all.
    """
    normalized = normalize_code(code)
    if not normalized or len(normalized) > CODE_MAX_LENGTH:
        # Bail before the query: an over-long string cannot match a
        # `varchar(CODE_MAX_LENGTH)` column, and answering it with the
        # same 404 keeps the not-found response uniform.
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.CODE_NOT_FOUND,
            "code not found",
        )

    row = db.execute(
        select(ObjectCode).where(
            ObjectCode.workspace_id == ws.id,
            ObjectCode.code == normalized,
        )
    ).scalar_one_or_none()
    if row is None:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.CODE_NOT_FOUND,
            "code not found",
        )
    return row
