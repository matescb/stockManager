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
`provider_wrote_custom_field_row` exists.** Since A3 (ADR-0034) both
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


def provider_wrote_custom_field_row(provider: str, row, *, is_primary: bool) -> bool:
    """Is this row *provider*'s to delete? The only ownership test that runs.

    Asked by the two operations that remove data — the reconcile's
    trailing "delete rows absent from my payload" pass, and unlink. Both
    need the same answer, and it is per ROW, not per key, because A3
    (ADR-0034) made both provider tiers write the same un-namespaced
    CANONICAL keys.

    Two branches, and each one is load-bearing in a direction the other
    is not:

    * a **canonical** key is the writer's, and only the writer's —
      `row.provider == provider`, with no claim on an unstamped row. An
      unstamped canonical row is nobody's: "no one recorded who wrote
      this" is not evidence that I did, and a secondary acting on that
      reading would hard-delete the rows the A5 backfill has not stamped
      yet. (WRITING to one is different and stays allowed: precedence,
      in `spec_schema.provider_outranks`, treats a NULL as claimable, so
      the first provider to answer a key takes it.)
    * every **other** key goes by namespace and tier, exactly as ADR-0031
      wrote it. It must NOT go by provenance: a row stamped `digikey`
      holding a bare `image_url` belongs to whoever is primary NOW, so
      after an admin switches the primary to Mouser,
      `DELETE /provider-links/digikey` would otherwise take the part's
      image and datasheet with it. It is also what still lets a primary
      prune the un-normalised bare rows every prod part carries.
    """
    # Imported lazily: `spec_schema` imports this module, so a
    # module-level import back into it would be circular.
    from app.domain.parts.spec_schema import all_canonical_keys

    if row.key in all_canonical_keys():
        return row.provider == provider
    return provider_owns_custom_field_key(provider, row.key, is_primary=is_primary)
