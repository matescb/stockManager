"""Catalogue lookup for agents: one MPN, every configured provider (SM-001).

What `POST /api/parts/lookup-mpn` does for the UI, held to the agent-path
rules of `provider_quota` (reserve, pacing, a `quota` object per
provider) and shaped for a model that has to decide whether the part it
read in a datasheet exists:

* **One record per provider, never merged.** DigiKey's terms require its
  data to be attributed and not aggregated "without distinction"; each
  record carries `source`, the provider's own `product_url`, unmodified,
  and `retrieved_at`.
* **`match` on every record.** `exact` only when the provider's MPN is
  the one asked for. DigiKey's keyword fallback and Mouser's partial
  match come back `fuzzy`, with the MPN actually found — the
  `provider-refresh` job had to forbid fuzzy matches for exactly the
  reason an agent needs to see them.
* **Nothing is persisted.** No part, no spec row, no asset download.
  The provider cache (`lookup_with_cache`) is the only state touched,
  and a hit there spends no quota and is reported as `cache: "hit"`.

A provider that refuses (reserve reached, suspended, upstream error) is
listed under `errors` with its stable code while the others still
answer. Only when NO provider produced an answer is the whole call
refused (`CatalogLookupRefused`), with the first provider's code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core.errors import ErrorCodes
from app.core.time import utcnow
from app.domain.parts.provider_credentials import active_credential_rows
from app.domain.parts.providers.base import match_kind
from app.domain.parts.services.provider_cache import (
    lookup_with_cache,
    peek_cached,
    suspended_for,
)
from app.domain.parts.services.provider_quota import (
    QUOTA,
    QuotaReserveReached,
    account_key,
)
from app.domain.parts.services.provider_refresh import (
    RefreshError,
    primary_provider_name,
    provider_target,
)
from app.domain.parts.services.provider_refresh_failures import looks_like_quota
from app.domain.provider_errors import ProviderError, ProviderSuspendedError

__all__ = ["CatalogLookupRefused", "PROVIDER_ORDER", "catalog_lookup"]

#: Answer order. DigiKey first, matching `PROVIDER_PRECEDENCE`; nothing is
#: merged, so this is presentation only.
PROVIDER_ORDER: tuple[str, ...] = ("digikey", "mouser")

_PRICE_KEY = re.compile(r"^Unit price \((\d+)\+\)$")
_COMPLIANCE_KEYS: tuple[str, ...] = (
    "RoHS", "REACH", "REACH SVHC", "MSL", "ECCN", "HTS code",
    "USHTS", "CAHTS", "CNHTS", "JPHTS", "KRHTS", "MXHTS", "BRHTS", "TARIC",
)
_FLAG_KEYS: tuple[tuple[str, str], ...] = (
    ("Discontinued", "discontinued"),
    ("End of life", "end_of_life"),
    ("NCNR", "ncnr"),
)


class CatalogLookupRefused(Exception):
    """No provider could answer; `code` is a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class _Answer:
    provider: str
    quota: dict[str, Any]
    cache: str = "miss"
    result: dict[str, Any] | None = None
    retrieved_at: Any = None
    error: dict[str, str] | None = None
    miss: str | None = None


def catalog_lookup(
    db,
    ws,
    *,
    mpn: str,
    manufacturer: str | None = None,
    provider: str = "all",
) -> dict[str, Any]:
    query = (mpn or "").strip()
    if not query:
        raise CatalogLookupRefused(ErrorCodes.PART_PROVIDER_MISSING_MPN, "mpn is empty")
    clients = _clients(db, ws, provider)
    answers = [_ask(ws.id, client, query) for client in clients]
    return _response(query, manufacturer, answers)


# ---------------------------------------------------------------------------
# which providers
# ---------------------------------------------------------------------------
def _clients(db, ws, provider: str) -> list[Any]:
    if provider != "all":
        try:
            return [provider_target(db, ws, provider).client]
        except RefreshError as exc:
            raise CatalogLookupRefused(
                ErrorCodes.PART_PROVIDER_NOT_CONFIGURED, exc.message
            ) from None
    names: list[str] = []
    primary = primary_provider_name(ws)
    if primary and primary != "none":
        names.append(primary)
    names += [row.provider for row in active_credential_rows(db, ws.id)]
    clients: dict[str, Any] = {}
    for name in names:
        if name in clients:
            continue
        try:
            clients[name] = provider_target(db, ws, name).client
        except RefreshError:
            continue  # named but not buildable: simply not asked
    if not clients:
        raise CatalogLookupRefused(
            ErrorCodes.PART_PROVIDER_NOT_CONFIGURED,
            "no distributor provider is configured for this workspace "
            "(the owner sets one in Workspace settings)",
        )
    return [clients[n] for n in sorted(clients, key=_order)]


def _order(name: str) -> int:
    return PROVIDER_ORDER.index(name) if name in PROVIDER_ORDER else len(PROVIDER_ORDER)


def _account(client: Any) -> str:
    return account_key(getattr(client, "client_id", None) or getattr(client, "api_key", None))


# ---------------------------------------------------------------------------
# one provider
# ---------------------------------------------------------------------------
def _ask(workspace_id: UUID, client: Any, mpn: str) -> _Answer:
    name, account = client.name, _account(client)
    entry = peek_cached(name, mpn)
    if entry is not None:
        result, retrieved_at = entry
        answer = _Answer(name, QUOTA.snapshot(name, account, spent=0), cache="hit")
        return _classify(answer, result, retrieved_at)

    left = suspended_for(name)
    if left:
        return _refused(name, account, ErrorCodes.PROVIDER_SUSPENDED,
                        f"{name} has suspended this application (HTTP 403); retry in {left}s")
    try:
        QUOTA.check_reserve(name, account)
    except QuotaReserveReached as exc:
        return _refused(name, account, ErrorCodes.PROVIDER_QUOTA_RESERVE_REACHED, str(exc))

    QUOTA.pace(workspace_id, name)
    before = QUOTA.calls_today(name, account)
    try:
        result = lookup_with_cache(client, mpn)
    except ProviderSuspendedError as exc:
        return _refused(name, account, ErrorCodes.PROVIDER_SUSPENDED, exc.message,
                        spent=QUOTA.calls_today(name, account) - before)
    except ProviderError as exc:
        return _refused(name, account, ErrorCodes.PROVIDER_UPSTREAM_ERROR, exc.message,
                        spent=QUOTA.calls_today(name, account) - before)
    spent = max(0, QUOTA.calls_today(name, account) - before)
    answer = _Answer(name, QUOTA.snapshot(name, account, spent=spent))
    cached = peek_cached(name, mpn)
    return _classify(answer, result, cached[1] if cached else utcnow())


def _refused(name: str, account: str, code: str, message: str, *, spent: int = 0) -> _Answer:
    answer = _Answer(name, QUOTA.snapshot(name, account, spent=max(0, spent)))
    answer.error = {"provider": name, "code": code, "message": message}
    return answer


def _classify(answer: _Answer, result: dict[str, Any], retrieved_at: Any) -> _Answer:
    """Sort a provider answer into record, miss, or refusal.

    A refusal must never read as a miss: "no match" tells the agent the
    part does not exist, which is the wrong conclusion from a 403 or 429.
    """
    message = result.get("message") or ""
    if result.get("found") and result.get("result"):
        answer.result = result
        answer.retrieved_at = retrieved_at
        return answer
    if result.get("suspended"):
        code = ErrorCodes.PROVIDER_SUSPENDED
    elif looks_like_quota(message):
        code = ErrorCodes.RATE_LIMITED
    elif "no match" in message.lower():
        answer.miss = message
        return answer
    else:
        code = ErrorCodes.PROVIDER_UPSTREAM_ERROR
    answer.error = {"provider": answer.provider, "code": code, "message": message}
    return answer


# ---------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------
def _response(query: str, manufacturer: str | None, answers: list[_Answer]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for a in answers:
        if a.result is None:
            continue
        shaped = [
            _record(a, row, query, manufacturer) for row in a.result.get("candidates") or []
        ]
        if shaped:
            candidates += shaped
            chosen = next((c for c in shaped if c.get("manufacturer_matches")), shaped[0])
            records.append(chosen)
        else:
            records.append(_record(a, a.result["result"], query, manufacturer,
                                   match=a.result.get("match")))

    errors = [a.error for a in answers if a.error]
    if not records and not any(a.miss for a in answers):
        first = errors[0]
        detail = "; ".join(f"{e['provider']}: {e['message']}" for e in errors)
        quotas = "; ".join(_quota_text(a.quota) for a in answers)
        raise CatalogLookupRefused(first["code"], f"{detail} (quota: {quotas})")

    out: dict[str, Any] = {
        "mpn": query,
        "match": _overall_match(records),
        "records": records,
        "cache": "hit" if answers and all(a.cache == "hit" for a in answers) else "miss",
        "quota": [a.quota for a in answers],
    }
    if candidates:
        out["candidates"] = candidates
    misses = [{"provider": a.provider, "message": a.miss} for a in answers if a.miss]
    if misses:
        out["misses"] = misses
    if errors:
        out["errors"] = errors
    return out


def _overall_match(records: list[dict[str, Any]]) -> str:
    if not records:
        return "none"
    return "exact" if any(r["match"] == "exact" for r in records) else "fuzzy"


def _quota_text(q: dict[str, Any]) -> str:
    return (
        f"{q['provider']} remaining_day={q['remaining_day']} "
        f"reserve={q['reserve']} source={q['source']}"
    )


def _record(
    answer: _Answer,
    rec: dict[str, Any],
    query: str,
    manufacturer: str | None,
    *,
    match: str | None = None,
) -> dict[str, Any]:
    specs = {row.get("key"): row.get("value") for row in rec.get("specs") or []}
    retrieved = answer.retrieved_at
    out: dict[str, Any] = {
        "source": answer.provider,
        "match": match_kind(query, rec.get("mpn")) if match is None else match,
        "product_url": rec.get("source_url") or None,
        "retrieved_at": retrieved.isoformat() if hasattr(retrieved, "isoformat") else retrieved,
        "mpn": rec.get("mpn"),
        "manufacturer": rec.get("manufacturer"),
        "description": rec.get("description"),
        "category": rec.get("category"),
        "lifecycle": _lifecycle(specs),
        "compliance": _compliance(specs),
        "datasheet_url": rec.get("datasheet_url"),
        "stock": _stock(specs),
        "price_breaks": _price_breaks(specs),
        "cache": answer.cache,
    }
    if manufacturer:
        out["manufacturer_matches"] = _same_maker(manufacturer, rec.get("manufacturer"))
    return out


def _same_maker(wanted: str, found: str | None) -> bool:
    a, b = wanted.strip().casefold(), (found or "").strip().casefold()
    return bool(a and b) and (a in b or b in a)


def _lifecycle(specs: dict[str, Any]) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    if specs.get("Lifecycle"):
        out["status"] = specs["Lifecycle"]
    for key, name in _FLAG_KEYS:
        if specs.get(key):
            out[name] = True
    if specs.get("Suggested replacement"):
        out["suggested_replacement"] = specs["Suggested replacement"]
    return out or None


def _compliance(specs: dict[str, Any]) -> dict[str, Any] | None:
    out = {k: specs[k] for k in _COMPLIANCE_KEYS if specs.get(k)}
    return out or None


def _stock(specs: dict[str, Any]) -> int | None:
    raw = specs.get("In stock (qty)")
    try:
        return int(str(raw).replace(",", "").strip()) if raw else None
    except ValueError:
        return None


def _price_breaks(specs: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key, value in specs.items():
        m = _PRICE_KEY.match(key or "")
        if m and value:
            rows.append({"qty": int(m.group(1)), "unit_price": value})
    return sorted(rows, key=lambda r: r["qty"])
