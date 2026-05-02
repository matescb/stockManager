"""All-models barrel coverage meta-test (issue #126 / CQ-010).

Lives separately from `test_migrations.py` (which is slow-marked
round-trip coverage) so this fast metadata check stays in the default
pytest run.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import app.domain  # noqa: F401 — needed to resolve __file__ below
import app.domain.all_models  # noqa: F401 — registers every model with Base.metadata
from app.infra.db import Base


def test_all_models_covers_every_domain():
    """Every `backend/app/domain/<pkg>/models.py` must be imported by
    `app.domain.all_models`.

    Without this, a contributor can add a new domain model module,
    forget to add it to the barrel, and Alembic's `--autogenerate`
    silently skips the table at deploy time. The barrel is the only
    seam between the model files on disk and the SQLAlchemy metadata
    that Alembic's `env.py` reads from.

    The check: walk a shallow glob of `domain/*/models.py`, import each
    module, then assert every class in that module that defines a
    `__tablename__` is present in `Base.metadata.tables` after
    `all_models` has done its job. A failing assertion names the model
    + module so the contributor knows exactly which line to add to
    `all_models.py`.
    """
    domain_root = Path(app.domain.__file__).resolve().parent
    models_files = sorted(domain_root.glob("*/models.py"))
    assert models_files, "no domain/*/models.py modules discovered"

    missing: list[str] = []
    seen_any_tablename = False
    for models_py in models_files:
        pkg = models_py.parent.name
        modname = f"app.domain.{pkg}.models"
        mod = importlib.import_module(modname)
        for cls_name, cls in vars(mod).items():
            if not isinstance(cls, type):
                continue
            tablename = getattr(cls, "__tablename__", None)
            if not tablename:
                # Mixins (e.g. WorkspaceOwned) and helpers without a
                # tablename are correctly excluded.
                continue
            # Only consider classes actually defined in the discovered
            # module — avoids re-counting `Workspace` re-exported from
            # somewhere else.
            if getattr(cls, "__module__", None) != modname:
                continue
            seen_any_tablename = True
            if tablename not in Base.metadata.tables:
                missing.append(f"{modname}.{cls_name} (table={tablename})")

    assert seen_any_tablename, (
        "discovery walked domain/*/models.py but found no `__tablename__`"
        " classes — the test's discovery is broken"
    )
    assert not missing, (
        "the following models are present on disk but missing from "
        "app.domain.all_models — add the import to that barrel:\n  - "
        + "\n  - ".join(missing)
    )
