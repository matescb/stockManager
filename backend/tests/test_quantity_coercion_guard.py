"""The quantity-coercion CI guard does what it claims.

A guard that silently stops matching is worse than no guard, and a guard
that flags legitimate integers gets disabled. So both directions are
pinned: the real app tree is clean, the shapes step 2 removed are caught,
and the integers that are genuinely integers are not.

Mirrors `test_stockentry_constructor_guard.py`, which loads its script
the same way.
"""
import importlib.util
import textwrap
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_quantity_coercions.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("quantity_coercion_checker", _SCRIPT)
    assert spec is not None
    checker = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(checker)  # type: ignore[union-attr]
    return checker


def _tree(tmp_path: Path, rel: str, source: str) -> Path:
    app_dir = tmp_path / "app"
    target = app_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(source))
    return app_dir


def test_clean_app_tree_passes():
    checker = _load_checker()
    app_dir = Path(__file__).resolve().parent.parent / "app"

    assert checker.check_app_tree(app_dir) == []


def test_float_on_a_named_quantity_is_flagged(tmp_path: Path):
    checker = _load_checker()
    app_dir = _tree(
        tmp_path,
        "api/routes/things.py",
        """\
        def serialize(entry):
            return {"quantity": float(entry.quantity_delta)}
        """,
    )

    violations = checker.check_app_tree(app_dir)

    assert [(v[1], v[2], v[3]) for v in violations] == [(2, "float", "quantity_delta")]


def test_int_truncation_is_flagged_through_a_default(tmp_path: Path):
    """`int(prior.quantity_delta or 0)` was the real shape in
    `parts_scan.py` — a `or` default must not hide the coercion."""
    checker = _load_checker()
    app_dir = _tree(
        tmp_path,
        "api/routes/scan.py",
        """\
        def row(prior):
            return {"quantity": int(prior.quantity_delta or 0)}
        """,
    )

    assert [(v[2], v[3]) for v in checker.check_app_tree(app_dir)] == [
        ("int", "quantity_delta")
    ]


def test_dict_subscript_quantity_is_flagged(tmp_path: Path):
    checker = _load_checker()
    app_dir = _tree(
        tmp_path,
        "domain/reports/service.py",
        """\
        def blocking(rows):
            return sum(1 for row in rows if int(row["short_by"]) > 0)
        """,
    )

    assert [(v[2], v[3]) for v in checker.check_app_tree(app_dir)] == [("int", "short_by")]


def test_kitting_quantity_names_are_covered(tmp_path: Path):
    """A guard can fail by being vacuously green, not just by being noisy.

    Kitting (#900) landed a whole new quantity surface — `at_staging`,
    `to_move`, `moving`, `wanted` — while this PR was in review. It carries
    `Decimal` correctly, so the guard passed over it; but it passed because
    it did not recognise any of those names, which means a future
    `int(to_move)` would have slipped straight through. Pin the names.
    """
    checker = _load_checker()
    app_dir = _tree(
        tmp_path,
        "domain/builds/kitting.py",
        """\
        def plan(line, required, wanted):
            a = int(line.at_staging)
            b = int(line.to_move)
            c = float(line.moving)
            d = int(required)
            e = int(wanted)
            return a, b, c, d, e
        """,
    )

    assert [(v[2], v[3]) for v in checker.check_app_tree(app_dir)] == [
        ("int", "at_staging"),
        ("int", "to_move"),
        ("float", "moving"),
        ("int", "required"),
        ("int", "wanted"),
    ]


def test_genuine_integers_are_not_flagged(tmp_path: Path):
    """`builds.quantity` stays an integer by design (you build 5 boards,
    not 5.5), money at a JSON boundary has to become a JSON number, and
    loop counters / status codes are not quantities at all. None of these
    may cost a reviewer a second's thought."""
    checker = _load_checker()
    app_dir = _tree(
        tmp_path,
        "domain/builds/service.py",
        """\
        import math

        def things(build, response, lot, offers, rows):
            n = int(build.build_qty)
            code = int(response.status_code)
            price = float(lot.purchase_unit_cost)
            page = int(offers.page_size)
            boards = math.ceil(rows[0]["short_by"])
            return n, code, price, page, boards
        """,
    )

    assert checker.check_app_tree(app_dir) == []


def test_noqa_pragma_suppresses_a_deliberate_exception(tmp_path: Path):
    checker = _load_checker()
    app_dir = _tree(
        tmp_path,
        "domain/sourcing/pricing.py",
        """\
        def normalise(item):
            # A distributor price break is an integer package count.
            return int(item.quantity)  # noqa: quantity-decimal
        """,
    )

    assert checker.check_app_tree(app_dir) == []


def test_stock_service_is_strict_about_every_coercion(tmp_path: Path):
    """`int(db.execute(q).scalar_one() or 0)` and `int(row[1])` name no
    quantity, so the name-based rule cannot see them — and those were the
    two sharpest casts in the codebase. Inside the ledger chokepoint the
    rule is "no coercions at all"."""
    checker = _load_checker()
    app_dir = _tree(
        tmp_path,
        "domain/stock/service.py",
        """\
        def current(db, q, rows):
            total = int(db.execute(q).scalar_one() or 0)
            per_part = {row[0]: int(row[1]) for row in rows}
            return total, per_part
        """,
    )

    assert [(v[1], v[2]) for v in checker.check_app_tree(app_dir)] == [
        (2, "int"),
        (3, "int"),
    ]


def test_the_quantity_helper_module_is_exempt(tmp_path: Path):
    """`quantity_out` *is* the JSON boundary — it has to produce a number,
    and it is the one place that coercion is reviewed."""
    checker = _load_checker()
    app_dir = _tree(
        tmp_path,
        "domain/_quantity.py",
        """\
        def quantity_out(value):
            whole = int(value)
            return whole if value == whole else float(value)
        """,
    )

    assert checker.check_app_tree(app_dir) == []
