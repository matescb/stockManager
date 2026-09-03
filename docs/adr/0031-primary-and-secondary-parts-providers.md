# ADR-0031: A workspace has one primary parts provider and any number of secondaries, each in its own field namespace

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-09-03
- **Supersedes**: —
- **Superseded by**: —

## Context

Until now a workspace picked exactly one parts provider. `workspaces.parts_provider`
named it, `parts_provider_api_key` / `_api_secret` held its credentials, and that
one provider owned everything a lookup produced: the part's `manufacturer`, `mpn`,
`footprint` and `description`, the `parts.linked_*` columns, and every
`custom_fields` row with `source='provider'`.

That is the right model for the fields a part can only have one of. It is the
wrong model for the fields the operator most wants a second opinion on. Price and
stock are per-distributor by nature: "DigiKey says 8 weeks, Mouser has 400 on the
shelf" is the question purchasing actually asks, and a single-provider workspace
can only answer half of it.

So we needed a second provider that contributes catalog data without becoming a
second claimant on the part's identity.

The obvious cheap approach — let both providers write the same
`source='provider'` rows — fails immediately, and fails destructively. The
existing reconciliation ends with:

> delete every `source='provider'` row whose key isn't in the payload I just
> fetched

Two providers sharing one keyspace means each refresh deletes the other's rows.
The operator sees data appear and vanish depending on which button they pressed
last, and nothing in the code says why.

## Decision

**One primary, many secondaries, and a namespace boundary between them.**

The PRIMARY provider is `workspaces.parts_provider`, unchanged in every respect.
It owns the part columns, `parts.linked_*`, the scan-import and lookup-mpn flows,
and the un-namespaced `source='provider'` custom fields. Its behaviour is
byte-for-byte what it was.

A SECONDARY provider gets three things and nothing else:

- a credentials row in `workspace_provider_credentials`;
- a link row in `part_provider_links` (`external_id`, `source_url`,
  `last_refresh_at`);
- custom fields under a `"{provider}:"` prefix — `mouser:source_url`,
  `mouser:Lead time`, and so on.

It writes **no part column at all**.

**Reconciliation is scoped to a namespace, and the scope is one function.**
`provider_fields.py::provider_owns_custom_field_key(provider, key, is_primary)`
is the whole rule: the primary owns every key that is not namespaced, a secondary
owns exactly its own prefix. The two sets are disjoint by construction, so the
delete pass physically cannot see another provider's rows. The refresh route
passes it as the `owns_key` argument to `_reconcile_provider_fields`; there is no
second place the boundary is expressed.

Only names in `KNOWN_PROVIDER_NAMES` count as a namespace, so an upstream spec
genuinely called `Vref:max` stays the primary's.

**The two credential stores stay separate, and each provider is in exactly
one of them.** The primary's key lives in the legacy
`workspaces.parts_provider_api_*` columns, written by `PATCH /api/workspaces/current`.
`workspace_provider_credentials` holds secondaries and nothing else: migration
0070 backfills no rows into it, and `PUT /current/provider-credentials` returns
`400 workspace.provider_is_primary` for a payload naming the workspace's own
`parts_provider`.

The rejected alternative — backfill the primary's key so one table covers both
tiers — reads tidier and is a two-writer trap. Nothing keeps the two stores in
sync, so clearing the row would report success while the columns kept
authenticating (an operator revoking a leaked key would have been told it
worked), and a workspace that later set `parts_provider` back to `none` would
still be reachable through `?provider=`, because the switch never clears the
legacy columns.

`credentials_for(db, ws, provider)` is therefore **the secondary resolution
point**, not a unification. Its primary fallback exists for the one caller that
must accept either tier behind a single name — the `?provider=` refresh. The
primary's own flows read the legacy columns directly, at four call sites:
`api/routes/parts_assets.py` (the primary refresh path), `api/routes/parts_provider.py`,
`api/routes/parts_scan.py`, and `domain/projects/bom_import_provider.py`.
Retiring the legacy columns means migrating those four first.

**The primary keeps its own unlink.** `DELETE /api/parts/{id}/provider-links/{provider}`
refuses the primary and points at `PATCH` with `unlink_provider=true`, because
releasing the primary also has to release the part columns — a different
operation that happens to share a noun.

That guard reads `ws.parts_provider`, **not** `p.linked_provider`. Which tier a
provider occupies is a workspace-level fact. `linked_provider` only records
which provider last drove a given part's columns, and it is sticky: it survives
an admin switching the workspace primary. Keying the guard off it would leave a
link the workspace's own configuration now calls a secondary permanently
unremovable, with no route able to touch it.

## Consequences

- Refreshing either provider is now safe in any order. `tests/test_secondary_provider.py`
  pins both directions; those two tests are the reason this ADR exists, and they
  should not be deleted without replacing the guarantee.
- A part can be linked to a secondary without a primary at all. The Sourcing tab
  therefore keys off `linked_provider || provider_links.length`, not
  `linked_provider` alone.
- Namespaced keys are catalog data by definition, so `providerCatalog.ts` routes
  them to the Sourcing tab and keeps them out of the user's Specs tab.
- Secondary refreshes do not download assets. The primary already owns the part's
  image and datasheet; a second content-addressed copy would cost a request per
  refresh to produce a field nothing renders. The upstream URL is stored as-is.
- The legacy `workspaces.parts_provider_api_*` columns stay. Dropping them would
  be a destructive migration straight to prod (no staging) for no functional gain,
  and they remain the primary's only store.
- A secondary silently drops a field whose namespaced key would overflow
  `custom_fields.key` (varchar 256). The prefix adds characters to an upstream
  name we don't control, and truncating the *key* would collide two different
  attributes onto one row, so the field is skipped and counted in the response's
  `summary.skipped` instead. Values are truncated as before; only keys are
  skipped.
- **Adding a provider** means exactly five edits, and nothing about the
  reconciliation changes:
  1. a branch in `providers/base.py::make_provider` (plus the client module);
  2. a name in `provider_fields.py::KNOWN_PROVIDER_NAMES`;
  3. the `Literal` in `ProviderCredentialsIn` and `WorkspacePatch`
     (`domain/workspaces/schemas.py`);
  4. an entry in `web/src/lib/providers.ts::PROVIDERS` — the frontend's single
     registry. Its label, whether it needs a client secret, its MPN search URL
     and the namespace regex in `providerCatalog.ts` are all derived from that
     one entry;
  5. a row in the provider table in [docs/domain/providers.md](../domain/providers.md).

## See also

- [ADR-0025](0025-universal-audit-log-policy.md) — the audit row this feature's
  mutations join.
- [ADR-0029](0029-api-tokens-and-csrf-exemption.md) — why the credentials route is
  session-cookie only.
- [docs/api/parts.md](../api/parts.md) — the `?provider=` contract.
- [docs/api/workspaces.md](../api/workspaces.md) — the credentials route.
