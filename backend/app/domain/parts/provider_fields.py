"""Which custom-field keys a provider owns, and whose namespace they sit in.

Two provider tiers write `custom_fields(source='provider')` rows on the
same part:

* the PRIMARY provider (`workspaces.parts_provider`) writes un-namespaced
  keys — `Resistance`, `image_url`, `source_url` — exactly as it always
  has;
* every SECONDARY provider writes keys prefixed `"{provider}:"` —
  `mouser:source_url`, `mouser:Resistance`.

Each refresh reconciles (inserts, updates, and *deletes*) the rows in its
own namespace and must be blind to every other namespace. The predicates
below are that boundary — `provider_owns_custom_field_key` is the single
place the rule is written down.

**Canonical keys are the one exception, and they are why
`provider_owns_custom_field_row` exists.** Since A3 (ADR-0034) both
tiers write the same un-namespaced canonical key — `resistance`, not
`mouser:Resistance` — because "load the specs from both DigiKey and
Mouser" is meaningless while a secondary's parametric data sits under a
prefix nothing reads. For those keys the prefix no longer identifies the
writer, so ownership is decided by the `custom_fields.provider` column
instead. Everything else keeps the prefix rule exactly as ADR-0031 wrote
it: a bare non-canonical key belongs to whoever is primary *now*, which
is what lets a new primary prune the old one's rows.
"""
from __future__ import annotations

PROVIDER_RESERVED_CUSTOM_FIELD_KEYS: tuple[str, ...] = (
    "image_url",
    "datasheet_url",
    "source_url",
)

PROVIDER_ASSET_CUSTOM_FIELD_KINDS: dict[str, str] = {
    "image_url": "image",
    "datasheet_url": "datasheet",
}

# Every provider name `make_provider` can build. A key prefixed with one
# of these belongs to that provider's namespace and is invisible to the
# primary reconciliation; anything else is the primary's.
#
# Keep in sync with `providers/base.py::make_provider` and the
# `parts_provider` Literal in `domain/workspaces/schemas.py`.
KNOWN_PROVIDER_NAMES: tuple[str, ...] = ("digikey", "mouser")

_NAMESPACE_SEPARATOR = ":"

# Width of `custom_fields.key` (see `domain/custom_fields/models.py`).
# Namespacing adds `len(provider) + 1` characters to an upstream field
# name we do not control, so a secondary refresh has to check the result
# fits before handing it to the DB — otherwise a long enough
# ProductAttributes name is an uncaught DataError, i.e. a 500.
CUSTOM_FIELD_KEY_MAX = 256


def is_provider_reserved_custom_field_key(key: str) -> bool:
    return key in PROVIDER_RESERVED_CUSTOM_FIELD_KEYS


def namespaced_custom_field_key(provider: str, key: str) -> str:
    """`("mouser", "Resistance") -> "mouser:Resistance"`."""
    return f"{provider}{_NAMESPACE_SEPARATOR}{key}"


def is_provider_namespaced_key(key: str) -> bool:
    """True when *key* sits in some known provider's namespace.

    Only the names in `KNOWN_PROVIDER_NAMES` count. An upstream spec
    genuinely called `Vref:max` stays the primary's — the prefix has to
    name a provider we can actually build.
    """
    return any(
        key.startswith(f"{name}{_NAMESPACE_SEPARATOR}") for name in KNOWN_PROVIDER_NAMES
    )


def provider_owns_custom_field_key(provider: str, key: str, *, is_primary: bool) -> bool:
    """Is *key* inside the namespace this refresh is allowed to reconcile?

    The primary owns every key that is NOT namespaced; a secondary owns
    exactly its own prefix. The two sets are disjoint by construction,
    which is what stops a DigiKey refresh from deleting the `mouser:`
    rows (and vice versa).
    """
    if is_primary:
        return not is_provider_namespaced_key(key)
    return key.startswith(f"{provider}{_NAMESPACE_SEPARATOR}")


def provider_owns_custom_field_row(provider: str, row, *, is_primary: bool) -> bool:
    """Is *row* inside the scope this refresh may reconcile and delete?

    `row` needs a `.key` and a `.provider` (`custom_fields`, alembic
    0081). For a CANONICAL key the answer is provenance: the row is
    yours if you wrote it, or if nobody did — `provider` is NULL on every
    row predating the column, and on the 9,377 prod rows A5 has not
    re-keyed yet, so the first provider to answer a canonical key claims
    it. For every other key the answer is the namespace rule above,
    unchanged.

    Splitting the two matters in both directions. Without the provenance
    branch a DigiKey refresh would delete the `resistance` row Mouser
    wrote, because it is un-namespaced and absent from DigiKey's payload
    — ADR-0031's original bug, reintroduced one level down. With the
    provenance branch applied to NON-canonical keys, a workspace that
    switched primary could never prune the old primary's bare rows, and
    a payload containing one would collide with it on `uq_cf_unique`.
    """
    # Imported lazily: `spec_schema` imports this module for
    # `PROVIDER_RESERVED_CUSTOM_FIELD_KEYS`, so a module-level import back
    # into it would be circular.
    from app.domain.parts.spec_schema import all_canonical_keys

    if row.key in all_canonical_keys():
        return row.provider is None or row.provider == provider
    return provider_owns_custom_field_key(provider, row.key, is_primary=is_primary)


def provider_wrote_custom_field_row(provider: str, row) -> bool:
    """Did *provider* contribute this row? The question UNLINK asks.

    Deliberately not the same question as
    `provider_owns_custom_field_row`, which asks what a refresh may
    reconcile. The difference is a row whose `provider` is NULL:

    * a refresh treats an unclaimed CANONICAL row as claimable, so the
      first provider to answer that key takes it, and a primary still
      prunes the un-normalised bare rows every prod part carries;
    * an unlink must treat it as somebody else's, because "nobody
      recorded who wrote this" is not evidence that *I* did. Deleting on
      that reading would let unlinking a secondary take the primary's
      legacy rows with it.

    So: a stamped row belongs to whoever is stamped, and an unstamped one
    belongs to this provider only if it sits in its namespace (which is
    how every pre-0081 secondary row looks).
    """
    if row.provider is not None:
        return row.provider == provider
    return row.key.startswith(f"{provider}{_NAMESPACE_SEPARATOR}")
