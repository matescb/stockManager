# Object Codes API

Audience: engineer

Universal short codes: every part, lot, storage location, order and build in
a workspace can carry one short, human-transcribable code. Scanning or typing
the code resolves back to that exact object — the data half of an
"ID-Anything" labelling scheme. Label rendering and printing are a separate
surface; this API owns the codes and the resolver only.

## Conventions

See [API conventions](./README.md) for envelope, errors, auth. Mounted at
`/api/codes` with `dependencies=_member_gate` (`backend/app/main.py` — the
POST needs member+, the GET passes for viewers). Rate-limited per workspace:
`60/minute` for minting, `120/minute` for resolving.

## Code format

Eight characters from [Crockford's base32](https://www.crockford.com/base32.html)
alphabet — `0-9` plus `A-Z` minus `I`, `L`, `O`, `U` — drawn from `secrets`
(a CSPRNG). Three properties drive that choice:

- **Transcribable.** The excluded letters are the ones people confuse with
  `1` and `0`; `U` is dropped so a random draw cannot spell an obscenity.
- **Dense in a QR.** All-uppercase-alphanumeric encodes in QR's alphanumeric
  mode rather than byte mode, so the printed symbol stays small.
- **Opaque.** The code is not derived from the row's UUID or a counter, so it
  leaks neither object counts nor ids. 32⁸ ≈ 1.1 × 10¹² per workspace.

A code is **not a secret** — it is printed on a label — but the resolver still
requires an authenticated session scoped to the owning workspace.

Lookup normalises the input before matching: upper-cases, strips grouping
hyphens/underscores/whitespace, and applies Crockford's decode aliases
(`I`/`L` → `1`, `O` → `0`). So `abcd-123o` finds `ABCD1230`.

## Model

Central polymorphic table `object_codes` (migration `0073`), not a `code`
column on five tables — see
[domain/polymorphic](../domain/polymorphic.md) for the shape and the
hard-delete contract.

`ObjectCodeOut` (`backend/app/domain/codes/schemas.py`): `code`,
`entity_type`, `entity_id`.

`entity_type` is a closed set, CHECK-constrained in the DB and a `Literal` in
the request schema: `build`, `lot`, `order`, `part`, `storage_location`.
`project` is deliberately absent — a code is a physical-world handle, and you
do not stick a label on a project.

Two uniqueness constraints:

| Constraint | Meaning |
|---|---|
| `uq_object_codes_ws_code` | A code is unique **per workspace**. Two workspaces may independently mint the same string; that is what lets the code stay short. |
| `uq_object_codes_ws_entity` | One code per object, forever. Also what makes get-or-create safe under concurrency — the losing INSERT re-reads the winner's code. |

## Routes

### `POST /api/codes`

| Field | Type | Required | Notes |
|---|---|---|---|
| `entity_type` | string | yes | One of the five above; anything else is `422`. |
| `entity_id` | UUID | yes | Must name a row in the caller's workspace. |

Get-or-create. Returns `200` (not `201`) with `ObjectCodeOut` — most calls
create nothing. Minting is **lazy**: nothing has a code until someone asks.

`404` when `entity_id` does not resolve in the caller's workspace. That check
runs *before* any insert: without it, a caller in workspace B could mint a
code against workspace A's part id and then resolve it, turning this endpoint
into a cross-tenant existence oracle.

`409` with `code` `code.mint_exhausted` if eight consecutive random draws all
collide — effectively unreachable, and a retry rather than a 5xx.

Audit: `object_code.minted`, written only on the call that actually mints.
`target_ids` carries `[object_code.id, entity_id]`; the comment is
`entity_type=<type>` — never the code itself.

### `GET /api/codes/{code}`

The scan path. Returns `200` with `ObjectCodeOut`.

`404` with `code` `code.not_found` for unknown, malformed, over-long and
other-workspace codes alike. One response for all four: splitting them would
tell a scanner which codes exist elsewhere.

## Frontend

`/c/:code` (`web/src/routes/codes/CodeResolve.tsx`) is the scan landing page —
a printed QR encodes that path. It calls the resolver and `replace`-navigates
to the object's own detail route (`/parts/{id}/info`, `/lots/{id}/info`,
`/storage/{id}/info`, `/orders/{id}`, `/builds/{id}`), so the URL after a scan
is the object's canonical URL and Back returns to where the user was. It sits
inside the auth `Gate`, so an unauthenticated scan round-trips through
`/login` and lands back on the code.

## Don't

- **Don't derive a code from the entity UUID or a counter.** Both leak; the
  CSPRNG draw plus retry-on-collision is the whole design.
- **Don't add a FK on `entity_id`.** It is polymorphic; hard-delete cleanup
  runs through `domain/_polymorphic_cleanup.py`, which registers
  `object_codes` alongside `attachments` / `custom_fields` / `tag_links`.
- **Don't widen `entity_type` without a migration.** The CHECK constraint,
  the `Literal` in `schemas.py` and `CODE_ENTITY_TYPES` all derive from one
  definition in `backend/app/domain/codes/models.py`.
- **Don't distinguish "unknown code" from "code in another workspace"** in
  any response, log line or timing-sensitive path.

## See also

- [domain/polymorphic](../domain/polymorphic.md) — the polymorphic-table contract
- [ADR-0002](../adr/0002-code-enforced-workspace-isolation.md) — workspace isolation is the caller's job
- `backend/app/domain/codes/README.md` — module orientation
