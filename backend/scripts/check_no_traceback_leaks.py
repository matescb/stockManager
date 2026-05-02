"""
SEC2-018 — CI guard: no traceback/stack-trace data in HTTPException(detail=…).

Uses the Python AST to walk every .py file under backend/app/ and flags any
call where:

  HTTPException(detail=<expr involving traceback / format_exc / __class__>)

The check is intentionally conservative — it looks at the *source text* of
the detail= argument, not runtime values, so it catches the most common
accidental patterns without producing false negatives from multi-line strings.

Exit code 0 → clean. Exit code 1 → at least one violation printed to stdout.
"""

import ast
import sys
from pathlib import Path

# Keywords whose presence inside the detail= argument text indicates a
# potential stack-trace leak.
_LEAK_KEYWORDS = (
    "traceback",
    "format_exc",
    "format_exception",
    "__class__",
    "exc_info",
)


def _detail_source(node: ast.Call, source_lines: list[str]) -> str | None:
    """Return the source text of the `detail=` keyword argument, or None."""
    for kw in node.keywords:
        if kw.arg == "detail":
            start = kw.value.col_offset
            end_line = kw.value.end_lineno  # type: ignore[attr-defined]
            end_col = kw.value.end_col_offset  # type: ignore[attr-defined]
            if kw.value.lineno == end_line:  # type: ignore[attr-defined]
                return source_lines[kw.value.lineno - 1][start:end_col]  # type: ignore[attr-defined]
            # Multi-line: join from start to end
            lines = []
            for i in range(kw.value.lineno - 1, end_line):  # type: ignore[attr-defined]
                lines.append(source_lines[i])
            return "\n".join(lines)
    return None


def check_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_no, snippet) violations in *path*."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    source_lines = source.splitlines()
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match HTTPException(…) — handle both bare name and attribute forms
        func = node.func
        is_http_exc = (
            (isinstance(func, ast.Name) and func.id == "HTTPException")
            or (isinstance(func, ast.Attribute) and func.attr == "HTTPException")
        )
        if not is_http_exc:
            continue

        detail_text = _detail_source(node, source_lines)
        if detail_text is None:
            continue

        for keyword in _LEAK_KEYWORDS:
            if keyword in detail_text:
                violations.append((node.lineno, detail_text.strip()))
                break  # one violation per call site is enough

    return violations


def main() -> int:
    # Resolve app/ relative to this script's location:
    # backend/scripts/check_no_traceback_leaks.py → backend/app/
    scripts_dir = Path(__file__).resolve().parent
    app_dir = scripts_dir.parent / "app"

    if not app_dir.is_dir():
        print(f"ERROR: app directory not found at {app_dir}", file=sys.stderr)
        return 2

    found_any = False
    for py_file in sorted(app_dir.rglob("*.py")):
        violations = check_file(py_file)
        for lineno, snippet in violations:
            rel = py_file.relative_to(scripts_dir.parent.parent)
            print(f"{rel}:{lineno}: HTTPException detail leaks stack info: {snippet!r}")
            found_any = True

    if found_any:
        print(
            "\nFAIL: HTTPException(detail=…) contains traceback / stack-trace keywords.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
