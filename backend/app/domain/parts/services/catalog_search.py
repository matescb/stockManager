"""Catalogue search for agents: keyword + parametric, nothing kept (SM-002).

`catalog_lookup` confirms one MPN; this answers "0603 C0G 100 nF, at
least 50 V, active, in stock". DigiKey's KeywordSearch takes category,
manufacturer, status and parametric filters and returns the ids it
accepts in its own `FilterOptions`, surfaced here as `facets` so the
agent narrows by re-querying with ids it was given rather than guessing.
Mouser has keyword search only; a DigiKey-only filter sent to Mouser is
refused with `provider.unsupported_filter`, never silently dropped.

Rules shared with `catalog_lookup`: one candidate list per provider,
labelled with `source`, the provider's own `product_url` and
`retrieved_at`, never merged; the agent-path quota (reserve, pacing,
`quota` per provider); a single attempt with transport retries off.

**Nothing is persisted** — no part, spec, cache or audit row. DigiKey's
terms forbid building "your own database" from its data, so the most kept
is an in-process TTL copy of a DigiKey response, per workspace and per
exact request, for ten minutes (and DigiKey's static category tree for a
day). Mouser's caching terms are unread, so its answers live for the
request only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, NoReturn
from uuid import UUID

import httpx

from app.core.errors import ErrorCodes
from app.core.time import utcnow
from app.domain.parts.providers import digikey_search, mouser_search
from app.domain.parts.providers.digikey_search import SORT_FIELDS, SearchQuery
from app.domain.parts.services.catalog_lookup import CatalogLookupRefused, configured_clients
from app.domain.parts.services.provider_cache import _TtlLruCache, guarded_call, suspended_for
from app.domain.parts.services.provider_quota import (
    QUOTA,
    PacingRefused,
    QuotaReserveReached,
    provider_account,
)
from app.domain.parts.services.provider_refresh_failures import looks_like_quota
from app.domain.provider_errors import ProviderError, ProviderSuspendedError
from app.domain.sourcing.providers._retry_transport import retries_disabled

__all__ = ["MAX_LIMIT", "SORTS", "catalog_search"]

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
SORTS: tuple[str, ...] = tuple(
    name for field in SORT_FIELDS for name in (field, f"-{field}")
)
_RESPONSE_TTL_SEC = 600.0
_CATEGORIES_TTL_SEC = 86_400.0

# DigiKey responses, keyed by workspace + request hash; never Mouser.
_responses = _TtlLruCache(maxsize=256, default_ttl=_RESPONSE_TTL_SEC)
# DigiKey's category tree, keyed by credential (it is the same for all).
_categories = _TtlLruCache(maxsize=16, default_ttl=_CATEGORIES_TTL_SEC)


@dataclass
class _Answer:
    provider: str
    quota: dict[str, Any]
    cache: str = "miss"
    result: dict[str, Any] | None = None
    retrieved_at: Any = None
    categories: list[dict[str, Any]] | None = None
    error: dict[str, Any] | None = None


def catalog_search(
    db,
    ws,
    *,
    keywords: str = "",
    category_id: str | None = None,
    manufacturer_ids: list[str] | None = None,
    parameter_filters: list[dict[str, Any]] | None = None,
    status: str | None = None,
    in_stock: bool = False,
    rohs: bool = False,
    has_datasheet: bool = False,
    sort: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    provider: str = "all",
    list_categories: bool = False,
) -> dict[str, Any]:
    query = _query(keywords, category_id, manufacturer_ids, parameter_filters, status,
                   in_stock, rohs, has_datasheet, sort, limit, offset, list_categories)
    digikey_only = _digikey_only_filters(query, list_categories)
    if provider == "mouser" and digikey_only:
        raise CatalogLookupRefused(
            ErrorCodes.PROVIDER_UNSUPPORTED_FILTER, _unsupported(digikey_only)
        )
    answers = []
    for client in configured_clients(db, ws, provider):
        if client.name == "mouser" and digikey_only:
            answers.append(_skipped(client, _unsupported(digikey_only)))
        else:
            answers.append(_ask(ws.id, client, query, list_categories))
    return _response(query, answers)


# ---------------------------------------------------------------------------
# arguments
# ---------------------------------------------------------------------------
def _query(keywords, category_id, manufacturer_ids, parameter_filters, status,
           in_stock, rohs, has_datasheet, sort, limit, offset, list_categories) -> SearchQuery:
    words = (keywords or "").strip()
    if not words and not list_categories:
        _invalid("keywords is required (or pass list_categories true to browse categories)")
    if sort is not None and sort not in SORTS:
        _invalid(f"sort must be one of {', '.join(SORTS)}")
    filters: list[tuple[int, tuple[str, ...]]] = []
    for item in parameter_filters or []:
        try:
            pid = int(item["parameter_id"])
            values = tuple(str(v) for v in item["value_ids"])
        except (KeyError, TypeError, ValueError):
            _invalid("parameter_filters items are {parameter_id, value_ids}")
        if not values:
            _invalid(f"parameter_filters: parameter {pid} has no value_ids")
        filters.append((pid, values))
    if filters and not category_id:
        _invalid("parameter_filters need category_id: DigiKey's parametric ids are per "
                 "category (take both from facets)")
    return SearchQuery(
        keywords=words,
        category_id=str(category_id) if category_id else None,
        manufacturer_ids=tuple(str(m) for m in manufacturer_ids or ()),
        status_ids=(str(status),) if status else (),
        parameter_filters=tuple(filters),
        in_stock=bool(in_stock),
        rohs=bool(rohs),
        has_datasheet=bool(has_datasheet),
        sort=sort,
        limit=max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT)),
        offset=max(0, int(offset or 0)),
    )


def _invalid(message: str) -> NoReturn:
    raise CatalogLookupRefused(ErrorCodes.PROVIDER_INVALID_FILTER, message)


def _digikey_only_filters(q: SearchQuery, list_categories: bool) -> list[str]:
    named = (
        ("category_id", q.category_id), ("manufacturer_ids", q.manufacturer_ids),
        ("parameter_filters", q.parameter_filters), ("status", q.status_ids),
        ("has_datasheet", q.has_datasheet), ("sort", q.sort),
        ("list_categories", list_categories),
    )
    return [name for name, value in named if value]


def _unsupported(names: list[str]) -> str:
    return (f"mouser supports keyword search only (with in_stock / rohs); "
            f"not {', '.join(names)}")


def _skipped(client: Any, message: str) -> _Answer:
    answer = _Answer(client.name, QUOTA.snapshot(client.name, provider_account(client), spent=0))
    answer.error = {"provider": client.name, "code": ErrorCodes.PROVIDER_UNSUPPORTED_FILTER,
                    "message": message}
    return answer


# ---------------------------------------------------------------------------
# one provider
# ---------------------------------------------------------------------------
def _ask(workspace_id: UUID, client: Any, q: SearchQuery, list_categories: bool) -> _Answer:
    name, account = client.name, provider_account(client)
    key = _request_key(workspace_id, q, list_categories)
    if name == "digikey":
        entry = _responses.get_entry("digikey", key)
        if entry is not None:
            (result, cats), retrieved_at = entry
            answer = _Answer(name, QUOTA.snapshot(name, account, spent=0), cache="hit",
                             result=result, retrieved_at=retrieved_at, categories=cats)
            return answer

    left = suspended_for(name, account)
    if left:
        return _refused(name, account, ErrorCodes.PROVIDER_SUSPENDED,
                        f"{name} has suspended this application (HTTP 403); retry in {left}s")
    try:
        QUOTA.check_reserve(name, account)
        QUOTA.pace(workspace_id, name)
    except QuotaReserveReached as exc:
        return _refused(name, account, ErrorCodes.PROVIDER_QUOTA_RESERVE_REACHED, str(exc))
    except PacingRefused as exc:
        answer = _refused(name, account, ErrorCodes.PROVIDER_PACING, str(exc))
        answer.error["retry_after_ms"] = exc.retry_after_ms
        return answer
    return _live(client, name, account, q, list_categories, key)


def _live(client: Any, name: str, account: str, q: SearchQuery,
          list_categories: bool, key: str) -> _Answer:
    before = QUOTA.calls_today(name, account)

    def refused(code: str, message: str) -> _Answer:
        return _refused(name, account, code, message,
                        spent=QUOTA.calls_today(name, account) - before)

    try:
        with retries_disabled():
            cats = _categories_for(client, account) if list_categories else None
            result = _search(client, q) if q.keywords else {"found": True, "total": 0,
                                                            "candidates": [], "facets": None,
                                                            "applied_filters": None}
    except ProviderSuspendedError as exc:
        return refused(ErrorCodes.PROVIDER_SUSPENDED, exc.message)
    except ProviderError as exc:
        return refused(ErrorCodes.RATE_LIMITED if looks_like_quota(exc.message)
                       else ErrorCodes.PROVIDER_UPSTREAM_ERROR, exc.message)
    except (httpx.HTTPError, ValueError) as exc:
        return refused(ErrorCodes.PROVIDER_UPSTREAM_ERROR,
                       f"{name} request failed ({type(exc).__name__})")
    spent = max(0, QUOTA.calls_today(name, account) - before)
    answer = _Answer(name, QUOTA.snapshot(name, account, spent=spent))
    if cats is not None and not cats.get("found"):
        result = cats
    if not result.get("found"):
        return _classify_refusal(answer, result)
    answer.result, answer.retrieved_at = result, utcnow()
    answer.categories = cats.get("categories") if cats else None
    if name == "digikey":
        _responses.set("digikey", key, (result, answer.categories), ttl=_RESPONSE_TTL_SEC)
    return answer


def _search(client: Any, q: SearchQuery) -> dict[str, Any]:
    if client.name == "digikey":
        return guarded_call(client, lambda: digikey_search.search(client, q))
    return guarded_call(client, lambda: mouser_search.search(
        client, keywords=q.keywords, limit=q.limit, offset=q.offset,
        in_stock=q.in_stock, rohs=q.rohs))


def _categories_for(client: Any, account: str) -> dict[str, Any]:
    cached = _categories.get("digikey", account)
    if cached is not None:
        return cached
    result = guarded_call(client, lambda: digikey_search.categories(client))
    if result.get("found"):
        _categories.set("digikey", account, result, ttl=_CATEGORIES_TTL_SEC)
    return result


def _request_key(workspace_id: UUID, q: SearchQuery, list_categories: bool) -> str:
    body = json.dumps([digikey_search.build_payload(q), list_categories], sort_keys=True)
    return f"{workspace_id}:{hashlib.sha256(body.encode()).hexdigest()}"


def _refused(name: str, account: str, code: str, message: str, *, spent: int = 0) -> _Answer:
    answer = _Answer(name, QUOTA.snapshot(name, account, spent=max(0, spent)))
    answer.error = {"provider": name, "code": code, "message": message}
    return answer


def _classify_refusal(answer: _Answer, result: dict[str, Any]) -> _Answer:
    message = result.get("message") or f"{answer.provider} refused the search"
    if result.get("suspended"):
        code = ErrorCodes.PROVIDER_SUSPENDED
    elif looks_like_quota(message):
        code = ErrorCodes.RATE_LIMITED
    else:
        code = ErrorCodes.PROVIDER_UPSTREAM_ERROR
    answer.error = {"provider": answer.provider, "code": code, "message": message}
    if result.get("retry_after_ms") is not None:
        answer.error["retry_after_ms"] = result["retry_after_ms"]
    return answer


# ---------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------
def _response(q: SearchQuery, answers: list[_Answer]) -> dict[str, Any]:
    answered = [a for a in answers if a.result is not None]
    errors = [a.error for a in answers if a.error]
    if not answered:
        first = errors[0]
        detail = "; ".join(f"{e['provider']}: {e['message']}" for e in errors)
        raise CatalogLookupRefused(first["code"], detail)

    candidates: list[dict[str, Any]] = []
    for a in answered:
        stamp = a.retrieved_at.isoformat() if hasattr(a.retrieved_at, "isoformat") else None
        for row in a.result.get("candidates") or []:
            candidates.append({"source": a.provider, **row, "retrieved_at": stamp})
    out: dict[str, Any] = {
        "keywords": q.keywords,
        "limit": q.limit,
        "offset": q.offset,
        "total": {a.provider: a.result.get("total") for a in answered},
        "candidates": candidates,
        "facets": next(({"source": a.provider, **a.result["facets"]}
                        for a in answered if a.result.get("facets")), None),
        "applied_filters": {a.provider: a.result.get("applied_filters") for a in answered},
        "cache": "hit" if all(a.cache == "hit" for a in answered) else "miss",
        "quota": [a.quota for a in answers],
    }
    cats = next((a.categories for a in answered if a.categories is not None), None)
    if cats is not None:
        out["categories"] = cats
    if errors:
        out["errors"] = errors
    return out
