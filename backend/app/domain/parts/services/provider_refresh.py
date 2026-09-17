"""One part, one provider: the refresh the route and the sweep both run.

`POST /api/parts/{id}/refresh-from-provider` used to hold this whole
sequence inline — resolve the client, look up the MPN, drive the part
columns on the primary tier, file the category, reconcile the specs,
upsert the link. The `provider-refresh` operator job needs exactly that
sequence for a few hundred parts, and a second copy of it would be a
second set of answers to the questions ADR-0031 and ADR-0034 settled
once (who owns which columns, whose namespace a key sits in, which tier
downloads assets).

So the sequence lives here and the route keeps its HTTP concerns. The
three conditions that used to `raise_http` in the middle of it are
exceptions now (`MissingMpnError`, `UnknownProviderError`,
`ProviderNotConfiguredError`), each carrying the message the route still
puts on the wire; `ProviderUpstreamError` propagates untouched, because
the route turns it into a status code and the job records it as one
part's failure and carries on.

What deliberately did NOT move: the response envelope, the rate limit,
the `missing_specs` badge, and the decision of which provider to ask
for. `provider_name=None` still means "the workspace's primary".
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.secrets import decrypt
from app.core.time import utcnow
from app.domain.categories.service import CategoryIndex
from app.domain.parts.models import Part, PartProviderLink
from app.domain.parts.part_type import sync_part_type_and_log_ids
from app.domain.parts.provider_credentials import credentials_for
from app.domain.parts.provider_fields import (
    KNOWN_PROVIDER_NAMES,
    PROVIDER_ASSET_CUSTOM_FIELD_KINDS,
)
from app.domain.parts.provider_links import get_link, upsert_link
from app.domain.parts.providers import PartsProvider, make_provider
from app.domain.parts.services.assets import fetch_provider_asset
from app.domain.parts.services.provider_cache import lookup_fresh
from app.domain.parts.services.spec_reconcile import (
    CategoryOutcome,
    ReconcileReport,
    apply_provider_category,
    reconcile_provider_specs,
)

__all__ = [
    "MissingMpnError",
    "ProviderNotConfiguredError",
    "ProviderTarget",
    "RefreshError",
    "RefreshOutcome",
    "UnknownProviderError",
    "primary_provider_name",
    "provider_target",
    "refresh_part",
]

#: Part columns the PRIMARY tier drives. Reported as `part_columns_changed`
#: so the sweep's CSV says what a refresh rewrote. `last_refresh_at` and
#: `updated_by` are left out on purpose: they move on every refresh, so
#: listing them would make every row look like a change.
_TRACKED_PART_COLUMNS: tuple[str, ...] = (
    "manufacturer",
    "mpn",
    "footprint",
    "description",
    "linked_provider",
    "linked_external_id",
    "part_type",
)


class RefreshError(Exception):
    """A refresh that could not be attempted at all.

    Carries the message the route puts on the wire, so moving the
    sequence out of the handler did not move the wording with it.
    """

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider


class MissingMpnError(RefreshError):
    """The part has no MPN to look up. 400 on the route."""


class UnknownProviderError(RefreshError):
    """`?provider=` named something `make_provider` cannot build. 422."""


class ProviderNotConfiguredError(RefreshError):
    """No credentials for the named tier. 400 on the route."""


@dataclass(frozen=True)
class ProviderTarget:
    """A built client plus which tier it occupies in this workspace.

    Resolving is separate from refreshing because the sweep builds one
    client per workspace and reuses it for every part: `DigiKeyProvider`
    caches its OAuth token on the instance, and a client rebuilt per part
    would spend one token request per part out of the same daily quota
    the lookups come from.
    """

    client: PartsProvider
    is_primary: bool


@dataclass(frozen=True)
class RefreshOutcome:
    """What one (part, provider) refresh did.

    `found` False with a `error` message is the ordinary miss — the
    provider has never heard of this MPN. No link row is created for one,
    and nothing on the part is touched.
    """

    provider: str
    is_primary: bool
    found: bool
    #: True when this refresh created the `part_provider_links` row
    #: rather than refreshing one that already existed.
    linked: bool
    part_columns_changed: tuple[str, ...]
    report: ReconcileReport | None
    category: CategoryOutcome | None
    #: Asset kinds stored locally this time (`image`, `datasheet`). A
    #: secondary never fetches any — the primary owns the part's files.
    assets_fetched: tuple[str, ...]
    #: Asset kinds the payload offers that this call did NOT download,
    #: because `fetch_assets=False`. Always empty on the request path;
    #: it is how a dry-run sweep reports what an apply would pull without
    #: pulling it. Never both this and `assets_fetched` on one outcome.
    assets_would_fetch: tuple[str, ...]
    link: PartProviderLink | None
    error: str | None


def primary_provider_name(ws) -> str | None:
    """The workspace's primary provider, normalised, or None."""
    return (ws.parts_provider or "").strip().lower() or None


def provider_target(db, ws, provider_name: str | None) -> ProviderTarget:
    """Build the client for *provider_name* in *ws*, or raise.

    `None` — or the workspace's own `parts_provider` — resolves to the
    PRIMARY, whose credentials live in the legacy
    `workspaces.parts_provider_api_*` columns (ADR-0031: that table is
    for secondaries, and the primary must never have two stores). Any
    other known name resolves through `provider_credentials`.
    """
    primary = primary_provider_name(ws)
    requested = (provider_name or "").strip().lower() or None
    is_primary = requested is None or requested == primary

    if is_primary:
        client = make_provider(
            ws.parts_provider,
            decrypt(ws.parts_provider_api_key),
            decrypt(ws.parts_provider_api_secret),
        )
        if client is None:
            raise ProviderNotConfiguredError(
                "no parts provider configured (set one in Workspace settings)"
            )
        return ProviderTarget(client=client, is_primary=True)

    if requested not in KNOWN_PROVIDER_NAMES:
        raise UnknownProviderError(
            f"unknown parts provider '{requested}'", provider=requested
        )
    creds = credentials_for(db, ws, requested)
    client = make_provider(requested, *creds) if creds is not None else None
    if client is None:
        raise ProviderNotConfiguredError(
            f"no credentials configured for '{requested}' "
            "(set them in Workspace settings)",
            provider=requested,
        )
    return ProviderTarget(client=client, is_primary=False)


def refresh_part(
    db,
    *,
    ws,
    part: Part,
    provider_name: str | None,
    user_id: UUID | None,
    request_id: str | None = None,
    category_index: CategoryIndex | None = None,
    target: ProviderTarget | None = None,
    require_exact_mpn: bool = False,
    fetch_assets: bool = True,
) -> RefreshOutcome:
    """Re-run this part's MPN against one provider and write what came back.

    The PRIMARY tier owns the part columns (manufacturer / mpn /
    footprint, and description unless locally edited), `parts.linked_*`
    and the un-namespaced provider custom fields, and it is the only tier
    that downloads assets. A SECONDARY writes no part column at all: a
    link row, the canonical specs, and catalog keys under its own
    `"{provider}:"` prefix. Both reconcile strictly inside their own
    namespace, so refreshing one never disturbs the other's rows.

    `target` skips credential resolution for a caller that already built
    the client (the sweep, once per workspace). `category_index` shares
    one snapshot of `part_categories` with a caller that has other
    questions for it. `require_exact_mpn` refuses a fuzzy hit — DigiKey
    falls back to a keyword search and Mouser matches partially, which is
    fine for a part a human asked about by name and is NOT fine for the
    sweep's `--link-missing-providers` pass, where a near miss would link
    the part to a different product and import its specs.

    Caller owns the transaction. Nothing here commits.
    """
    mpn = (part.mpn or "").strip()
    if not mpn:
        raise MissingMpnError("part has no MPN to look up")

    resolved = target if target is not None else provider_target(db, ws, provider_name)
    client = resolved.client
    is_primary = resolved.is_primary

    # `lookup_fresh`, not `lookup_with_cache` — a refresh is somebody
    # explicitly asking for what upstream says now. The fresh result is
    # written back to the cache so later cached readers see it.
    out = lookup_fresh(client, mpn)
    result = out.get("result")
    if not out.get("found") or not result:
        return _miss(client.name, is_primary, out.get("message") or "no match")
    if require_exact_mpn and not _is_exact(result, mpn):
        return _miss(
            client.name,
            is_primary,
            f"no exact match for MPN (provider answered {result.get('mpn') or '?'})",
        )

    before = {name: getattr(part, name, None) for name in _TRACKED_PART_COLUMNS}
    assets: tuple[str, ...] = ()
    would_fetch: tuple[str, ...] = ()
    if is_primary:
        _apply_primary_columns(
            part, result=result, provider_name=client.name, user_id=user_id
        )
        extra_fields, assets, would_fetch = _asset_fields(
            result, ws, fetch=fetch_assets
        )
        # AFTER the asset downloads, not before: the audit write flushes,
        # and flushing here would hold this `parts` row's lock across two
        # remote downloads (image + datasheet, up to 45s each).
        sync_part_type_and_log_ids(
            db, ws_id=ws.id, user_id=user_id, part=part, request_id=request_id
        )
    else:
        extra_fields = _secondary_fields(result)

    # Category before specs — it selects the spec schema, and both tiers
    # may fill a category the part does not have yet. One the user chose
    # is never overruled.
    category = apply_provider_category(
        db,
        ws_id=ws.id,
        part=part,
        provider_name=client.name,
        provider_category=result.get("category"),
        description=result.get("description"),
        user_id=user_id,
        index=category_index,
    )
    report = reconcile_provider_specs(
        db,
        ws_id=ws.id,
        part=part,
        provider_name=client.name,
        raw_specs=[
            ((s.get("key") or ""), (s.get("value") or ""))
            for s in (result.get("specs") or [])
        ],
        category_slug=category.slug,
        is_primary=is_primary,
        user_id=user_id,
        description=result.get("description"),
        extra_fields=extra_fields,
        request_id=request_id,
        category_assigned=category.assigned,
    )

    existing = get_link(
        db, workspace_id=ws.id, part_id=part.id, provider=client.name
    )
    link = upsert_link(
        db,
        workspace_id=ws.id,
        part_id=part.id,
        user_id=user_id,
        provider=client.name,
        external_id=result.get("mpn"),
        source_url=str(result["source_url"]) if result.get("source_url") else None,
        last_refresh_at=part.last_refresh_at if is_primary else None,
    )

    return RefreshOutcome(
        provider=client.name,
        is_primary=is_primary,
        found=True,
        linked=existing is None,
        part_columns_changed=tuple(
            name
            for name in _TRACKED_PART_COLUMNS
            if getattr(part, name, None) != before[name]
        ),
        report=report,
        category=category,
        assets_fetched=assets if is_primary else (),
        assets_would_fetch=would_fetch if is_primary else (),
        link=link,
        error=None,
    )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _miss(provider: str, is_primary: bool, message: str) -> RefreshOutcome:
    """A lookup that answered nothing. No link row is created for one —
    a part the provider has never heard of is not linked to it."""
    return RefreshOutcome(
        provider=provider,
        is_primary=is_primary,
        found=False,
        linked=False,
        part_columns_changed=(),
        report=None,
        category=None,
        assets_fetched=(),
        assets_would_fetch=(),
        link=None,
        error=message,
    )


def _is_exact(result: dict, mpn: str) -> bool:
    return (result.get("mpn") or "").strip().casefold() == mpn.casefold()


def _apply_primary_columns(
    part: Part, *, result: dict, provider_name: str, user_id: UUID | None
) -> None:
    """Drive the columns the PRIMARY tier owns.

    Split out so `refresh_part` reads as the four steps it is. The asset
    download deliberately stays at the call site: it has to run before
    the `part_type` audit flush, and that ordering is load-bearing enough
    to be visible there rather than buried in here.
    """
    part.manufacturer = result.get("manufacturer") or part.manufacturer
    new_mpn = result.get("mpn") or part.mpn
    if new_mpn:
        part.mpn = new_mpn
    footprint = result.get("footprint")
    if footprint:
        # On every refresh we let the provider drive footprint — same
        # treatment as manufacturer/mpn (provider-owned for linked parts).
        part.footprint = footprint
    if not part.description_locally_edited:
        new_description = result.get("description")
        if new_description:
            part.description = new_description
    part.linked_provider = provider_name
    part.linked_external_id = result.get("mpn") or part.linked_external_id
    part.last_refresh_at = utcnow()
    part.updated_by = user_id


def _asset_fields(
    result: dict, ws, *, fetch: bool
) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]:
    """The primary's non-spec rows, plus what was (or would be) downloaded.

    Returns `(fields, fetched, would_fetch)`. A failed download keeps the
    upstream URL — the same fallback bulk-import uses. A SECONDARY gets
    none of this on purpose (ADR-0031): the primary already owns the
    part's image and datasheet, so a second content-addressed copy would
    cost a request per refresh to produce a field nothing renders.

    `fetch=False` is the planning pass. It stores the upstream URL, which
    is what a failed download would have stored anyway, and names the
    kinds it skipped. This is the ONE side effect in the refresh that a
    transaction cannot take back: a downloaded file is on disk in
    `UPLOAD_DIR` whatever the session does afterwards, so a dry run that
    did it would leave content-addressed orphans behind and spend a real
    HTTP request per asset to learn nothing it could not report from the
    payload.
    """
    fields: dict[str, str] = {}
    fetched: list[str] = []
    skipped: list[str] = []
    for key, asset_kind in PROVIDER_ASSET_CUSTOM_FIELD_KINDS.items():
        if not result.get(key):
            continue
        if not fetch:
            fields[key] = str(result[key])
            skipped.append(asset_kind)
            continue
        local = fetch_provider_asset(result[key], str(ws.id), asset_kind)
        fields[key] = local or result[key]
        if local:
            fetched.append(asset_kind)
    if result.get("source_url"):
        fields["source_url"] = str(result["source_url"])
    return fields, tuple(fetched), tuple(skipped)


def _secondary_fields(result: dict) -> dict[str, str]:
    """A secondary's non-spec rows, still bare here —
    `reconcile_provider_specs` applies the `"{provider}:"` prefix and the
    key-width guard."""
    return {
        key: str(result[key])
        for key in ("source_url", "datasheet_url", "category")
        if result.get(key)
    }
