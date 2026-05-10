from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.errors import raise_http
from app.core.secrets import decrypt
from app.domain.parts.providers import make_provider
from app.domain.parts.services.provider_cache import lookup_with_cache
from app.domain.parts.services.provider_import import create_from_provider_lookup
from app.domain.projects.models import Project, ProjectEntry
from app.domain.projects.schemas import (
    BomProviderCandidate,
    BomProviderFailure,
    BomProviderImportOut,
    BomProviderPendingChoice,
)


def import_unmatched_from_provider(
    db,
    *,
    workspace,
    user_id: UUID | None,
    project: Project,
    entry_ids: list[UUID] | None = None,
) -> BomProviderImportOut:
    provider = _provider_or_409(workspace)
    rows = _load_unmatched_entries(
        db,
        workspace_id=workspace.id,
        project_id=project.id,
        entry_ids=entry_ids,
    )

    created = 0
    pending: list[BomProviderPendingChoice] = []
    failures: list[BomProviderFailure] = []

    for entry in rows:
        outcome = _lookup_entry(provider, entry)
        if isinstance(outcome, BomProviderFailure):
            failures.append(outcome)
            continue
        if isinstance(outcome, BomProviderPendingChoice):
            pending.append(outcome)
            continue

        try:
            with db.begin_nested():
                _create_and_link(
                    db,
                    workspace_id=workspace.id,
                    user_id=user_id,
                    provider_name=provider.name,
                    entry=entry,
                    lookup_result=outcome,
                )
        except Exception as exc:
            failures.append(
                _failure(entry, _entry_mpn(entry), f"{type(exc).__name__}: {exc}")
            )
            continue
        created += 1

    return BomProviderImportOut(
        created=created,
        pending_choices=pending,
        failures=failures,
        provider=provider.name,
    )


def commit_provider_import_choices(
    db,
    *,
    workspace,
    user_id: UUID | None,
    project: Project,
    choices: dict[UUID, str],
) -> BomProviderImportOut:
    provider = _provider_or_409(workspace)
    rows = _load_unmatched_entries(
        db,
        workspace_id=workspace.id,
        project_id=project.id,
        entry_ids=list(choices.keys()),
    )
    by_id = {row.id: row for row in rows}

    created = 0
    failures: list[BomProviderFailure] = []
    for entry_id, manufacturer in choices.items():
        entry = by_id.get(entry_id)
        if entry is None:
            continue

        mpn = _entry_mpn(entry)
        lookup = _lookup_raw(provider, mpn)
        record = _record_for_manufacturer(lookup, manufacturer)
        if record is None:
            failures.append(
                _failure(entry, mpn, f"manufacturer not found: {manufacturer}")
            )
            continue

        try:
            with db.begin_nested():
                _create_and_link(
                    db,
                    workspace_id=workspace.id,
                    user_id=user_id,
                    provider_name=provider.name,
                    entry=entry,
                    lookup_result=record,
                )
        except Exception as exc:
            failures.append(
                _failure(entry, mpn, f"{type(exc).__name__}: {exc}")
            )
            continue
        created += 1

    return BomProviderImportOut(
        created=created,
        pending_choices=[],
        failures=failures,
        provider=provider.name,
    )


def _provider_or_409(workspace):
    provider = make_provider(
        workspace.parts_provider,
        decrypt(workspace.parts_provider_api_key),
        decrypt(workspace.parts_provider_api_secret),
    )
    if provider is None:
        raise_http(
            409,
            "bom_provider.no_provider",
            "no provider configured",
        )
    return provider


def _load_unmatched_entries(
    db,
    *,
    workspace_id: UUID,
    project_id: UUID,
    entry_ids: list[UUID] | None,
) -> list[ProjectEntry]:
    stmt = (
        select(ProjectEntry)
        .where(ProjectEntry.workspace_id == workspace_id)
        .where(ProjectEntry.project_id == project_id)
        .where(ProjectEntry.entry_type == "unmatched")
        .where(ProjectEntry.part_id.is_(None))
        .order_by(ProjectEntry.order_index)
    )
    if entry_ids is not None:
        stmt = stmt.where(ProjectEntry.id.in_(entry_ids))
    return list(db.execute(stmt).scalars())


def _lookup_entry(
    provider,
    entry: ProjectEntry,
) -> dict | BomProviderPendingChoice | BomProviderFailure:
    mpn = _entry_mpn(entry)
    if not mpn:
        return _failure(entry, mpn, "missing MPN")

    lookup = _lookup_raw(provider, mpn)
    if not lookup.get("found") or not lookup.get("result"):
        return _failure(entry, mpn, lookup.get("message") or "no match")

    candidates = _candidate_records(lookup)
    manufacturers = {(_manufacturer(candidate) or "").casefold() for candidate in candidates}
    manufacturers.discard("")
    if len(manufacturers) > 1:
        return BomProviderPendingChoice(
            entry_id=entry.id,
            mpn=mpn,
            candidates=[_candidate_out(candidate) for candidate in candidates],
        )
    return lookup["result"]


def _lookup_raw(provider, mpn: str) -> dict:
    try:
        return lookup_with_cache(provider, mpn)
    except Exception as exc:
        return {"found": False, "result": None, "message": f"provider raised {type(exc).__name__}"}


def _candidate_records(lookup: dict) -> list[dict]:
    candidates = lookup.get("candidates") or []
    if candidates and isinstance(candidates, list):
        return [candidate for candidate in candidates if isinstance(candidate, dict)]
    result = lookup.get("result")
    return [result] if isinstance(result, dict) else []


def _record_for_manufacturer(lookup: dict, manufacturer: str) -> dict | None:
    if not lookup.get("found") or not lookup.get("result"):
        return None
    wanted = manufacturer.strip().casefold()
    for candidate in _candidate_records(lookup):
        if (_manufacturer(candidate) or "").casefold() == wanted:
            return candidate
    result = lookup.get("result")
    if isinstance(result, dict) and (_manufacturer(result) or "").casefold() == wanted:
        return result
    return None


def _create_and_link(
    db,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    provider_name: str,
    entry: ProjectEntry,
    lookup_result: dict,
) -> None:
    mpn = _entry_mpn(entry)
    part = create_from_provider_lookup(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        provider_name=provider_name,
        mpn=mpn,
        lookup_result=lookup_result,
    )
    entry.part_id = part.id
    entry.entry_type = "part"
    entry.updated_by = user_id
    db.flush()


def _entry_mpn(entry: ProjectEntry) -> str:
    return (entry.name or "").strip()


def _manufacturer(record: dict) -> str | None:
    value = record.get("manufacturer")
    return str(value).strip() if value else None


def _candidate_out(record: dict) -> BomProviderCandidate:
    return BomProviderCandidate(
        manufacturer=_manufacturer(record) or "",
        mpn=(record.get("mpn") or None),
        description=(record.get("description") or None),
        source_url=(record.get("source_url") or None),
        image_url=(record.get("image_url") or None),
    )


def _failure(entry: ProjectEntry, mpn: str, reason: str) -> BomProviderFailure:
    return BomProviderFailure(entry_id=entry.id, mpn=mpn, reason=reason)
