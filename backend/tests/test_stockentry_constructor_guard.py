import importlib.util
import textwrap
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_stockentry_constructors.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("stockentry_checker", _SCRIPT)
    assert spec is not None
    checker = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(checker)  # type: ignore[union-attr]
    return checker


def test_clean_app_tree_passes():
    checker = _load_checker()
    app_dir = Path(__file__).resolve().parent.parent / "app"

    violations = checker.check_app_tree(app_dir)

    assert violations == []


def test_offending_app_file_detected(tmp_path: Path):
    checker = _load_checker()
    app_dir = tmp_path / "app"
    route_dir = app_dir / "api" / "routes"
    route_dir.mkdir(parents=True)
    bad_py = route_dir / "stock_shortcut.py"
    bad_py.write_text(
        textwrap.dedent(
            """\
            from app.domain.stock.models import StockEntry

            def bad_insert():
                return StockEntry(quantity_delta=1)
            """
        )
    )

    violations = checker.check_app_tree(app_dir)

    assert [(path.relative_to(app_dir), lineno) for path, lineno in violations] == [
        (Path("api/routes/stock_shortcut.py"), 4)
    ]


def test_allowed_service_file_not_flagged(tmp_path: Path):
    checker = _load_checker()
    app_dir = tmp_path / "app"
    service_dir = app_dir / "domain" / "stock"
    service_dir.mkdir(parents=True)
    service_py = service_dir / "service.py"
    service_py.write_text(
        textwrap.dedent(
            """\
            from app.domain.stock.models import StockEntry

            def add_stock():
                return StockEntry(quantity_delta=1)
            """
        )
    )

    violations = checker.check_app_tree(app_dir)

    assert violations == []
