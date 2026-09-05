"""CI guard: quantities are never laundered through ``float`` or ``int``.

Units-of-measure track, step 2. Migration ``0074`` widened every stock /
BOM / order quantity column to ``Numeric(18, 6)``, and step 2 removed the
coercions that used to squash each ledger sum back down to a machine
integer. Both coercions destroy data the column can now hold, and they do
it silently:

* ``float(qty)`` — binary floating point cannot represent ``0.1``, so an
  exact decimal that goes through a ``float`` comes back subtly wrong.
  Multiply it by a price and the error lands in money.
* ``int(qty)`` — truncates towards zero. ``12.5`` becomes ``12``, and the
  half-metre is gone with no error anywhere.

Neither is visible today, because nothing in the API can write a
fractional quantity yet. That is exactly why this guard exists now: the
window between "the column is wide" and "the input is open" is the only
time these can be removed with no behaviour to regress, and a
re-introduced one would sit dormant until the day it starts eating real
measured stock.

**Scope.** Deliberately narrow — a guard everyone disables is worse than
no guard. Two rules:

1. Everywhere under ``backend/app/``, it fires only when the *name being
   coerced says it is a quantity*: an attribute or variable from
   `_QUANTITY_NAMES`, or a dict subscript with a constant key from the
   same set. `float(unit_price)` at a JSON boundary,
   `int(response.status_code)`, `int(build_qty)` and every loop counter
   are invisible to it.
2. Inside `_STRICT_APP_PATHS` — today just `domain/stock/service.py` —
   **any** `int()` or `float()` is a violation, regardless of what it is
   applied to. That file is the ledger read/write chokepoint the whole
   invariant rests on (`CLAUDE.md`: every quantity read goes through
   `current_quantity`), and its casts were the sharpest of the lot:
   `int(db.execute(q).scalar_one() or 0)` and `int(row[1])` name nothing
   at all, so rule 1 cannot see them. It has no legitimate need for
   either coercion, so the cheapest correct rule is "none".

Integers that are genuinely integers stay integers:

* ``builds.quantity`` — you build 5 boards, not 5.5 (uom design §1.12), so
  ``build_qty`` / ``build_quantity`` are not quantity names here.
* whole-board counts (``_can_build_now``) and distributor package counts
  (``SourcingPriceBreak.quantity`` is an external contract) round
  *explicitly*, at their own boundary, from an exact Decimal — which is
  what ``math.ceil(...)`` on a Decimal expresses and ``int(...)`` does
  not.

**Escape hatch.** Append ``# noqa: quantity-decimal`` to the line for a
deliberate exception, mirroring the ``# noqa: tls-verify`` convention the
httpx guard already uses. Explain why in a comment: the pragma is the
place a reviewer looks for the argument.
"""

import ast
import sys
from pathlib import Path

_NOQA = "# noqa: quantity-decimal"

#: Attribute and variable names that denote a quantity of stock. Every one
#: of these either is, or is derived from, a column migration 0074 widened
#: to ``Numeric(18, 6)``.
#:
#: Keep this in step with new quantity-bearing code. ``at_staging`` /
#: ``to_move`` / ``moving`` / ``wanted`` are kitting's (#900) names: that
#: PR happens to carry ``Decimal`` correctly throughout, but the guard was
#: green over it for the wrong reason — it did not recognise a single one
#: of those names — so a future ``int(to_move)`` would have slipped
#: through. A vacuously-passing guard is the failure mode to watch for
#: here, not a noisy one.
#:
#: ``take`` / ``remaining`` / ``unclaimed`` / ``planned`` /
#: ``alternates_available`` are the pick list's (Track B4) names, added for
#: the same reason. ``take`` and ``remaining`` were already carrying real
#: quantities in ``kitting.py`` and ``builds/service.py`` before the pick
#: list existed, so the set was silent over those two as well.
_QUANTITY_NAMES = frozenset(
    {
        "actual_quantity",
        "alternates_available",
        "at_staging",
        "attrition_min_quantity",
        "available",
        "avail",
        "current_qty",
        "low_stock_report_quantity",
        "moving",
        "on_hand",
        "planned",
        "purchase_quantity",
        "qty",
        "quantity",
        "quantity_delta",
        "quantity_ordered",
        "quantity_received",
        "remaining",
        "required",
        "reserved",
        "short_by",
        "sub_avail",
        "take",
        "to_move",
        "total_on_hand",
        "unclaimed",
        "wanted",
    }
)

#: Constant dict keys that carry a quantity — `int(row["short_by"])` is
#: the shape the reports service used before step 2.
_QUANTITY_KEYS = _QUANTITY_NAMES | {"substitute_available", "threshold"}

_COERCIONS = frozenset({"float", "int"})

#: Files exempt from the guard. `_quantity.py` *is* the boundary: its
#: `quantity_out` has to produce a JSON number, so the one legitimate
#: coercion in the codebase lives there where it can be reviewed once.
_EXEMPT_APP_PATHS = frozenset({Path("domain/_quantity.py")})

#: Files where *every* int()/float() is a violation, named or not — see
#: rule 2 in the module docstring.
_STRICT_APP_PATHS = frozenset({Path("domain/stock/service.py")})


def _quantity_name(node: ast.AST) -> str | None:
    """The quantity name `node` refers to, or None if it isn't one.

    Unwraps the two shapes a defaulted read takes — ``qty or 0`` and
    ``qty if qty is not None else 0`` — so a default doesn't hide the
    coercion underneath it.
    """
    if isinstance(node, ast.BoolOp) and node.values:
        return _quantity_name(node.values[0])
    if isinstance(node, ast.IfExp):
        return _quantity_name(node.body)
    if isinstance(node, ast.Attribute) and node.attr in _QUANTITY_NAMES:
        return node.attr
    if isinstance(node, ast.Name) and node.id in _QUANTITY_NAMES:
        return node.id
    if isinstance(node, ast.Subscript):
        index = node.slice
        if (
            isinstance(index, ast.Constant)
            and isinstance(index.value, str)
            and index.value in _QUANTITY_KEYS
        ):
            return index.value
    return None


def _violation(node: ast.AST, *, strict: bool) -> tuple[int, str, str] | None:
    """`(lineno, coercion, quantity_name)` if `node` launders a quantity."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Name) or func.id not in _COERCIONS:
        return None
    if len(node.args) != 1 or node.keywords:
        return None
    name = _quantity_name(node.args[0])
    if name is None:
        if not strict:
            return None
        name = ast.unparse(node.args[0])
    return node.lineno, func.id, name


def check_file(path: Path, *, app_dir: Path) -> list[tuple[int, str, str]]:
    """Quantity coercions in `path`, as `(lineno, coercion, name)`."""
    rel = path.resolve().relative_to(app_dir.resolve())
    if rel in _EXEMPT_APP_PATHS:
        return []
    strict = rel in _STRICT_APP_PATHS

    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    lines = source.splitlines()
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        found = _violation(node, strict=strict)
        if found is None:
            continue
        # Accept the pragma anywhere the call spans, so a wrapped call can
        # carry it on whichever line reads best.
        start = found[0]
        end = getattr(node, "end_lineno", None) or start
        span = lines[start - 1 : end]
        if any(_NOQA in line for line in span):
            continue
        out.append(found)
    return sorted(out)


def check_app_tree(app_dir: Path) -> list[tuple[Path, int, str, str]]:
    violations: list[tuple[Path, int, str, str]] = []
    for py_file in sorted(app_dir.rglob("*.py")):
        for lineno, coercion, name in check_file(py_file, app_dir=app_dir):
            violations.append((py_file, lineno, coercion, name))
    return violations


def main() -> int:
    scripts_dir = Path(__file__).resolve().parent
    backend_dir = scripts_dir.parent
    app_dir = backend_dir / "app"

    if not app_dir.is_dir():
        print(f"ERROR: app directory not found at {app_dir}", file=sys.stderr)
        return 2

    violations = check_app_tree(app_dir)
    for path, lineno, coercion, name in violations:
        rel = path.relative_to(backend_dir.parent)
        print(f"{rel}:{lineno}: {coercion}({name}) discards quantity precision")

    if violations:
        print(
            "\nFAIL: quantity columns are Numeric(18, 6) since migration 0074.\n"
            "  float() loses exactness; int() truncates a measured quantity.\n"
            "  Keep the Decimal, and use app/domain/_quantity.py::quantity_out\n"
            f"  at the JSON boundary. Annotate with `{_NOQA}` if a whole\n"
            "  number really is the right type here (say why in a comment).",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
