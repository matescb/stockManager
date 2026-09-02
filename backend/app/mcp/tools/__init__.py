"""Every MCP tool, in one registry.

Importing the tool modules is what populates `_registry.REGISTRY` —
the `@tool` decorator registers on import. `load_tools()` makes that
import explicit rather than relying on a bare side-effecting import at
the bottom of a module, which a linter would strip and nobody would
notice until the tool list came back empty.
"""
from __future__ import annotations

from app.mcp.tools._registry import REGISTRY, ToolSpec


def load_tools() -> list[ToolSpec]:
    """The registered tools, importing the modules that define them."""
    from app.mcp.tools import (  # noqa: F401  (imported for side effect)
        read,
        sourcing,
        write,
        write_inventory,
    )

    return list(REGISTRY)


__all__ = ["load_tools", "ToolSpec"]
