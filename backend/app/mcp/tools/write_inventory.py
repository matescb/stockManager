"""Mutating MCP tools for inventory: stock movements and categories.

The KiCad-library write tools are in `write.py`; these are split off
because they share nothing with them but the write gate, and one module
covering both was doing two unrelated jobs.

Two contracts these share with their REST counterparts:

* **The ledger.** Stock is never written directly. Every quantity change
  goes through `domain/stock/service.py`, which owns the advisory locks,
  the storage constraints and the non-negativity trigger.
* **Audit.** A stock movement writes NO audit row, matching the REST
  routes, which write none either: the `stock_entries` row is itself the
  record and carries its own `created_by`. Adding one here would make
  the two surfaces disagree about what a stock movement is. Category
  creation does write one, because `POST /api/categories` does.
"""
from __future__ import annotations

from typing import Any

from app.domain._quantity import quantity_out
from app.domain.categories import service as categories_service
from app.domain.categories.schemas import PartCategoryIn
from app.domain.stock import service as stock_service
from app.domain.stock.schemas import AddStockIn, MoveStockIn, RemoveStockIn
from app.domain.stock.service import StockConflictError, StockError
from app.mcp.principal import Caller
from app.mcp.tools._registry import ToolError, tool
from app.mcp.tools._shared import (
    audit,
    compact,
    part_url,
    resolve_part,
    resolve_storage,
    sid,
)

# Ceilings, matched to the REST twins so a tool's cost does not depend
# on which door it came through. `api/routes/categories.py` limits its
# whole router to 30/minute; the stock routes are ungated per-route, so
# 60 is the read default halved — these are writes, and an agent that
# needs more than one stock movement a second is looping on a mistake.
_STOCK_RATE = "60/minute"
_CATEGORY_RATE = "30/minute"


@tool(writes=True, rate=_STOCK_RATE)
def add_stock(
    caller: Caller,
    part_id: str,
    qty: int,
    storage_location_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Add stock for a part — a delivery, a return, a correction upwards.

    Args:
        part_id: The part's id or exact MPN.
        qty: How many to add. Must be positive.
        storage_location_id: Where they went, from
            `list_storage_locations`. Required when the part has a
            mandatory default location and none is passed.
        note: A short free-text note recorded against the movement.

    Returns the resulting ledger entry and the part's new `on_hand`.
    """
    part = resolve_part(caller, part_id)
    _positive(qty)
    location = (
        resolve_storage(caller, storage_location_id) if storage_location_id else None
    )
    entry = _stock_call(
        stock_service.add_stock,
        caller,
        AddStockIn(
            part_id=part.id,
            quantity=qty,
            storage_location_id=location.id if location else None,
            comments=note,
        ),
    )
    return _stock_out(caller, part, entry)


@tool(writes=True, rate=_STOCK_RATE)
def consume_stock(
    caller: Caller,
    part_id: str,
    qty: int,
    storage_location_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Consume stock for a part — used on a build, scrapped, sold.

    Args:
        part_id: The part's id or exact MPN.
        qty: How many to consume. Must be positive.
        storage_location_id: Which location to take them out of, from
            `list_storage_locations`. Stock is tracked per location, so
            omitting this consumes from the unassigned pool only — NOT
            from wherever the part happens to be. Call `get_part` first
            and pass the location holding the stock.
        note: A short free-text note recorded against the movement.

    Fails with `stock.operation_error` when there is not enough
    available stock in that location; the message says how much there
    is. Returns the resulting ledger entry and the part's new
    `on_hand`.
    """
    part = resolve_part(caller, part_id)
    _positive(qty)
    location = (
        resolve_storage(caller, storage_location_id) if storage_location_id else None
    )
    entry = _stock_call(
        stock_service.remove_stock,
        caller,
        RemoveStockIn(
            part_id=part.id,
            quantity=qty,
            storage_location_id=location.id if location else None,
            comments=note,
        ),
    )
    return _stock_out(caller, part, entry)


@tool(writes=True, rate=_STOCK_RATE)
def move_stock(
    caller: Caller,
    part_id: str,
    qty: int,
    from_location_id: str,
    to_location_id: str,
) -> dict[str, Any]:
    """Move stock of a part from one storage location to another.

    Records a paired movement out of one location and into the other;
    the total on hand does not change.

    Args:
        part_id: The part's id or exact MPN.
        qty: How many to move. Must be positive.
        from_location_id: The location to take them from.
        to_location_id: The location to put them in. Refused if that
            location is full, or if its constraints do not allow this
            part.

    Returns the part's stock by location after the move.
    """
    part = resolve_part(caller, part_id)
    _positive(qty)
    source = resolve_storage(caller, from_location_id)
    target = resolve_storage(caller, to_location_id)
    _stock_call(
        stock_service.move_stock,
        caller,
        MoveStockIn(
            part_id=part.id,
            quantity=qty,
            source_storage_location_id=source.id,
            destination_storage_location_id=target.id,
        ),
    )
    return {
        "part_id": sid(part.id),
        "part_url": part_url(part.id),
        "moved": qty,
        "from_location": source.name,
        "to_location": target.name,
        "on_hand": stock_service.current_quantity(
            caller.db, workspace_id=caller.ws.id, part_id=part.id
        ),
    }


def _positive(qty: int) -> None:
    if qty <= 0:
        raise ToolError("stock.operation_error: qty must be a positive whole number")


def _stock_call(fn, caller: Caller, payload):
    """Call a stock service function, mapping its errors like the routes do.

    `stock.py`'s handlers turn `StockConflictError` into a 409 and
    `StockError` into a 400; both carry a message written for a human
    ("insufficient stock (have 3, want 10)") that is exactly what an
    agent needs to decide what to do instead. So the mapping is
    preserved rather than flattened into a generic failure.
    """
    try:
        return fn(
            caller.db,
            workspace_id=caller.ws.id,
            user_id=caller.user.id,
            payload=payload,
        )
    except StockConflictError as exc:
        raise ToolError(f"stock.constraint_violation: {exc}") from None
    except StockError as exc:
        raise ToolError(f"stock.operation_error: {exc}") from None


def _stock_out(caller: Caller, part, entry) -> dict[str, Any]:
    return compact(
        {
            "part_id": sid(part.id),
            "part_url": part_url(part.id),
            "entry_id": sid(entry.id),
            "quantity_delta": quantity_out(entry.quantity_delta),
            "storage_location_id": sid(entry.storage_location_id),
            "on_hand": stock_service.current_quantity(
                caller.db, workspace_id=caller.ws.id, part_id=part.id
            ),
        }
    )


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------


@tool(writes=True, rate=_CATEGORY_RATE)
def create_category(
    caller: Caller,
    name: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a part category.

    Args:
        name: The category's display name, e.g. "Resistors".
        description: An optional longer description.

    The category's `slug` is derived from the name and is what other
    tools take as `category_slug`; it also names the KiCad symbol
    library the category's parts are published into. Fails with
    `category.name_conflict` or `category.slug_conflict` if one already
    exists — call `list_categories` first.
    """
    payload = PartCategoryIn(name=name, description=description)
    row = categories_service.create_category(
        caller.db, ws=caller.ws, user_id=caller.user.id, payload=payload
    )
    audit(
        caller,
        action="category.created",
        target_type="part_category",
        target_id=row.id,
        comment="fields=" + ",".join(sorted(payload.model_fields_set)),
    )
    return compact(
        {
            "id": sid(row.id),
            "name": row.name,
            "slug": row.library_slug,
            "description": row.description,
        }
    )
