"""Forward-looking lint: alembic migrations must be self-contained
snapshots of schema-at-revision. They shouldn't reach into live
application code (`app.<...>`) because doing so couples the migration
to whichever app revision is checked out at upgrade time. If the
imported helper later renames or refactors, replaying the migration
on a fresh DB (CI clean checkout, dev reset, disaster recovery)
breaks.

DB-010 / issue #101 traces the original sin to migration `0016`'s
`from app.core.secrets import encrypt, safe_decrypt`. The companion
fix (this PR) adds the frozen shim `app.core._secrets_v0016` and a
signature-pinning test. This test pins the *convention* — any future
migration may only `from app.<...>` if the import target matches a
`_v\\d{4}` (or `_vNNNN`) frozen-shim pattern.

`0016` itself is the explicit known exception — the migration is
already on prod and per CLAUDE.md cannot be edited. The test
allow-lists it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


_VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# Migrations on `main` that pre-date this convention. They are
# allow-listed so the lint can be enforced going forward without
# rewriting historical migrations (which CLAUDE.md forbids anyway).
_KNOWN_EXCEPTIONS = {
    "0016_encrypt_workspace_secrets.py",
}

_FROZEN_SHIM_RE = re.compile(r"^app\.core\._secrets_v\d{4}$|^app\.core\._\w+_v\d{4}$")


def _migration_files() -> list[Path]:
    return sorted(p for p in _VERSIONS_DIR.glob("*.py") if not p.name.startswith("__"))


@pytest.mark.parametrize(
    "migration_path",
    _migration_files(),
    ids=lambda p: p.name,
)
def test_migration_does_not_import_live_app_code(migration_path: Path) -> None:
    if migration_path.name in _KNOWN_EXCEPTIONS:
        pytest.skip(
            f"{migration_path.name} pre-dates the lint and is on prod; "
            "see test_secrets_signature_pinning.py for its safety net"
        )

    source = migration_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(migration_path))

    bad_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith("app.") or mod == "app":
                # Only allow imports targeting a frozen `_vNNNN` shim.
                if not _FROZEN_SHIM_RE.match(mod):
                    bad_imports.append(f"line {node.lineno}: from {mod} import …")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app.") or alias.name == "app":
                    if not _FROZEN_SHIM_RE.match(alias.name):
                        bad_imports.append(f"line {node.lineno}: import {alias.name}")

    assert not bad_imports, (
        f"{migration_path.name} imports live app code; migrations must "
        f"be self-contained or import from a frozen `_vNNNN` shim. "
        f"Offending imports: {bad_imports}"
    )
