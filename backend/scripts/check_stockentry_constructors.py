"""
AUD-055 — CI guard: StockEntry() constructors stay in ledger-writing services.

The stock ledger is append-only. New StockEntry rows should be created only by
the stock, orders, and builds services, where workspace checks and operation
semantics are centralized. This guard scans backend/app/ and fails on raw
StockEntry() construction anywhere else.
"""

import ast
import sys
from pathlib import Path

_ALLOWED_APP_PATHS = {
    Path("domain/stock/service.py"),
    Path("domain/orders/service.py"),
    Path("domain/builds/service.py"),
}


def _is_stockentry_call(node: ast.Call) -> bool:
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "StockEntry") or (
        isinstance(func, ast.Attribute) and func.attr == "StockEntry"
    )


def check_file(path: Path, *, app_dir: Path) -> list[int]:
    """Return line numbers where *path* constructs StockEntry outside the allowlist."""
    rel = path.resolve().relative_to(app_dir.resolve())
    if rel in _ALLOWED_APP_PATHS:
        return []

    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_stockentry_call(node)
    ]


def check_app_tree(app_dir: Path) -> list[tuple[Path, int]]:
    violations: list[tuple[Path, int]] = []
    for py_file in sorted(app_dir.rglob("*.py")):
        for lineno in check_file(py_file, app_dir=app_dir):
            violations.append((py_file, lineno))
    return violations


def main() -> int:
    scripts_dir = Path(__file__).resolve().parent
    backend_dir = scripts_dir.parent
    app_dir = backend_dir / "app"

    if not app_dir.is_dir():
        print(f"ERROR: app directory not found at {app_dir}", file=sys.stderr)
        return 2

    violations = check_app_tree(app_dir)
    for path, lineno in violations:
        rel = path.relative_to(backend_dir.parent)
        print(f"{rel}:{lineno}: StockEntry() construction is not allow-listed")

    if violations:
        allowed = ", ".join(str(path) for path in sorted(_ALLOWED_APP_PATHS))
        print(
            "\nFAIL: StockEntry() may only be constructed in approved services: "
            f"{allowed}.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
