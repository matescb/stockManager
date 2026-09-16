"""The `category-seed` job — plan A6 (the tree) and B3 (the `Device:*`
symbols it carries).

Three groups:

* **The data** — every seed row has to be something the API itself would
  have accepted (`PartCategoryIn`), and every `value_template`
  placeholder has to be a canonical spec key for the category the path
  resolves to. Without that second check a template renders empty
  forever and nobody finds out until a schematic is open.
* **The rules** — idempotent, additive, never renames, never
  re-parents, never overwrites, and a dry run writes nothing.
* **Isolation** — two workspaces, `--workspace` narrowing, and no row
  from one appearing in the other's report.
"""
from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli.run_job import main as run_job_main
from app.domain.audit.models import AuditLog
from app.domain.categories import seed as seed_module
from app.domain.categories.models import PartCategory
from app.domain.categories.schemas import PartCategoryIn
from app.domain.categories.seed import (
    AUDIT_ACTION,
    CREATED,
    CSV_COLUMNS,
    REASON_RACE,
    SKIPPED,
    UNCHANGED,
    UPDATED,
    iter_seed_paths,
    run_category_seed,
    seed_all_workspaces,
)
from app.domain.categories.seed_tables import SEED_CATEGORIES, SEEDABLE_FIELDS
from app.domain.eda.value_template import placeholder_keys
from app.domain.parts.spec_schema import category_slug_for, spec_keys_for
from app.domain.workspaces.models import Workspace
from app.main import app
from tests._factories import signup_user


def _new_workspace(db, email: str | None = None) -> tuple[Workspace, TestClient]:
    """A signed-up workspace plus a client authenticated to it."""
    client = TestClient(app)
    response = signup_user(client, email=email)
    ws = db.get(Workspace, uuid.UUID(response.json()["data"]["workspace_id"]))
    assert ws is not None
    return ws, client


@pytest.fixture
def owned(db) -> tuple[Workspace, TestClient]:
    return _new_workspace(db)


@pytest.fixture
def other(db) -> tuple[Workspace, TestClient]:
    """A second workspace, for the isolation probes."""
    return _new_workspace(db)


def _categories(db, ws: Workspace) -> dict[str, PartCategory]:
    rows = db.execute(
        select(PartCategory).where(PartCategory.workspace_id == ws.id)
    ).scalars()
    return {row.name: row for row in rows}


def _seed(db, ws: Workspace, *, apply: bool = True):
    return seed_all_workspaces(db, apply=apply, workspace_id=ws.id)


def _create(client: TestClient, name: str, **body) -> dict:
    r = client.post("/api/categories", json={"name": name, **body})
    assert r.status_code == 201, r.text
    return r.json()["data"]


# ---------------------------------------------------------------------
# The seed data itself
# ---------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEED_CATEGORIES, ids=lambda s: s.library_slug)
def test_every_seed_row_is_a_valid_category_payload(seed):
    """The seed writes rows the API could have written — same slug shape,
    same placeholder grammar, same `kicad_fields` cap."""
    payload = PartCategoryIn(
        name=seed.name,
        description=seed.description,
        sort_order=seed.sort_order,
        refdes_prefix=seed.refdes_prefix,
        default_symbol_ref=seed.default_symbol_ref,
        footprint_filters=list(seed.footprint_filters or []) or None,
        value_template=seed.value_template,
        kicad_fields=list(seed.kicad_fields or []) or None,
        library_slug=seed.library_slug,
    )
    assert payload.library_slug == seed.library_slug


def test_seed_slugs_are_unique():
    slugs = [seed.library_slug for seed in SEED_CATEGORIES]
    assert len(set(slugs)) == len(slugs)


def test_seed_names_are_unique():
    """`uq_part_categories_ws_name` is workspace-global, not
    sibling-scoped — two seed rows sharing a name could never both be
    created."""
    names = [seed.name.lower() for seed in SEED_CATEGORIES]
    assert len(set(names)) == len(names)


def test_seed_parents_are_listed_before_their_children():
    seen: set[str] = set()
    for seed in SEED_CATEGORIES:
        if seed.parent is not None:
            assert seed.parent in seen, f"{seed.name} precedes its parent"
        seen.add(seed.name)


@pytest.mark.parametrize(
    "seed",
    [s for s in SEED_CATEGORIES if s.value_template or s.kicad_fields],
    ids=lambda s: s.library_slug,
)
def test_template_and_field_keys_are_canonical_for_the_category(seed):
    """A placeholder outside the category's spec schema renders empty for
    every part, forever. The path is resolved exactly the way the import
    path resolves it."""
    path = f"{seed.parent} / {seed.name}" if seed.parent else seed.name
    slug = category_slug_for(path)
    assert slug is not None, f"{path} does not resolve to a spec-schema slug"
    known = {spec.key for spec in spec_keys_for(slug)}

    assert placeholder_keys(seed.value_template) <= known
    assert set(seed.kicad_fields or ()) <= known


def test_passive_categories_point_at_kicads_stock_device_library():
    """B3: the symbol refs are KiCad's own, so no symbol bytes ship for a
    passive and the chooser shows one per class."""
    refs = {
        seed.name: seed.default_symbol_ref
        for seed in SEED_CATEGORIES
        if seed.default_symbol_ref
    }
    assert refs["Resistors"] == "Device:R"
    assert refs["Ceramic"] == "Device:C"
    assert refs["Electrolytic"] == "Device:C_Polarized"
    assert refs["Tantalum"] == "Device:C_Polarized"
    assert refs["Zener"] == "Device:D_Zener"
    assert refs["MOSFET N"] == "Device:Q_NMOS_GDS"
    assert all(ref.startswith("Device:") for ref in refs.values())


def test_ambiguous_roots_carry_no_symbol():
    """A bare "Capacitors" could be ceramic or electrolytic, and drawing
    a polarised part unpolarised is a schematic error."""
    by_name = {seed.name: seed for seed in SEED_CATEGORIES}
    assert by_name["Capacitors"].default_symbol_ref is None
    assert by_name["Transistors"].default_symbol_ref is None
    assert by_name["Capacitors"].refdes_prefix == "C"


# ---------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------


def test_seed_creates_the_tree_and_a_second_run_creates_nothing(db, owned):
    ws, _client = owned

    first = _seed(db, ws)
    assert [o.action for o in first] == [CREATED] * len(SEED_CATEGORIES)

    created = _categories(db, ws)
    assert created["Resistors"].default_symbol_ref == "Device:R"
    assert created["Resistors"].value_template == "{resistance} {tolerance} {package}"
    assert created["Ceramic"].parent_id == created["Capacitors"].id
    assert created["Ceramic"].library_slug == "capacitors-ceramic"
    assert created["Electrolytic"].kicad_fields[0] == "capacitance"

    second = _seed(db, ws)
    assert [o.action for o in second] == [UNCHANGED] * len(SEED_CATEGORIES)
    assert len(_categories(db, ws)) == len(SEED_CATEGORIES)


def test_dry_run_writes_nothing(db, owned):
    ws, _client = owned

    outcomes = _seed(db, ws, apply=False)
    assert [o.action for o in outcomes] == [CREATED] * len(SEED_CATEGORIES)

    db.flush()
    assert _categories(db, ws) == {}
    assert _audit_rows(db) == []


def test_dry_run_does_not_fill_an_existing_category(db, owned):
    """The plan mutates a *persistent* row if it is not careful, and a
    later autoflush would then write it."""
    ws, client = owned
    _create(client, "Resistors")

    outcomes = _seed(db, ws, apply=False)
    by_path = {o.path: o for o in outcomes}
    assert by_path["Resistors"].action == UPDATED
    assert "default_symbol_ref" in by_path["Resistors"].detail

    db.flush()
    db.expire_all()
    assert _categories(db, ws)["Resistors"].default_symbol_ref is None


def test_an_existing_root_is_filled_in_but_never_duplicated(db, owned):
    ws, client = owned
    existing = _create(client, "Resistors", description="mine")

    outcomes = _seed(db, ws)
    by_path = {o.path: o for o in outcomes}
    assert by_path["Resistors"].action == UPDATED
    assert by_path["Resistors"].category_id == uuid.UUID(existing["id"])

    rows = _categories(db, ws)
    assert rows["Resistors"].id == uuid.UUID(existing["id"])
    # Filled where it was unset...
    assert rows["Resistors"].default_symbol_ref == "Device:R"
    assert rows["Resistors"].refdes_prefix == "R"
    # ...and left alone where the user had already spoken.
    assert rows["Resistors"].description == "mine"


def test_user_values_are_never_overwritten(db, owned):
    ws, client = owned
    _create(
        client,
        "Resistors",
        refdes_prefix="RR",
        default_symbol_ref="MyLib:R",
        value_template="{resistance}",
        kicad_fields=["resistance"],
        footprint_filters=["MINE_*"],
    )

    _seed(db, ws)

    row = _categories(db, ws)["Resistors"]
    assert row.refdes_prefix == "RR"
    assert row.default_symbol_ref == "MyLib:R"
    assert row.value_template == "{resistance}"
    assert row.kicad_fields == ["resistance"]
    assert row.footprint_filters == ["MINE_*"]


def test_an_explicit_empty_kicad_fields_list_is_a_user_value(db, owned):
    """`[]` means "emit no symbol fields" and stops the inheritance walk
    in `kicad_specs.py`. It is set, not unset."""
    ws, client = owned
    _create(client, "Resistors", kicad_fields=[])

    _seed(db, ws)

    assert _categories(db, ws)["Resistors"].kicad_fields == []


def test_a_renamed_child_is_not_duplicated_but_reported(db, owned):
    """The user's own "Elcos" is not the seed's "Electrolytic", so the
    seed creates its own — unless the *name* is taken, which is the case
    this pins: a root called "Ceramic" blocks "Capacitors / Ceramic"
    because the unique index is workspace-global."""
    ws, client = owned
    mine = _create(client, "Ceramic", library_slug="my-ceramic")

    outcomes = _seed(db, ws)
    by_path = {o.path: o for o in outcomes}
    assert by_path["Capacitors / Ceramic"].action == SKIPPED
    assert "name used by another category" in by_path["Capacitors / Ceramic"].detail

    rows = _categories(db, ws)
    assert rows["Ceramic"].id == uuid.UUID(mine["id"])
    assert rows["Ceramic"].parent_id is None
    assert rows["Ceramic"].default_symbol_ref is None


def test_a_users_own_nesting_is_respected_not_rebuilt(db, owned):
    """Capacitors filed under a Passives umbrella still owns the bucket;
    the leaves hang off it rather than off a second root."""
    ws, client = owned
    passives = _create(client, "Passives")
    capacitors = _create(client, "Capacitors", parent_id=passives["id"])

    _seed(db, ws)

    rows = _categories(db, ws)
    assert rows["Capacitors"].id == uuid.UUID(capacitors["id"])
    assert rows["Capacitors"].parent_id == uuid.UUID(passives["id"])
    assert rows["Ceramic"].parent_id == uuid.UUID(capacitors["id"])


def test_a_taken_library_slug_is_reported_not_worked_around(db, owned):
    ws, client = owned
    _create(client, "Chip resistors", library_slug="resistors")

    outcomes = _seed(db, ws)
    by_path = {o.path: o for o in outcomes}
    assert by_path["Resistors"].action == SKIPPED
    assert "library slug used by another category" in by_path["Resistors"].detail
    assert "Resistors" not in _categories(db, ws)


def test_an_archived_category_is_left_retired(db, owned):
    ws, client = owned
    created = _create(client, "Resistors")
    archived = client.post(f"/api/categories/{created['id']}/archive")
    assert archived.status_code == 200, archived.text

    outcomes = _seed(db, ws)
    by_path = {o.path: o for o in outcomes}
    assert by_path["Resistors"].action == SKIPPED
    assert "archived" in by_path["Resistors"].detail

    rows = _categories(db, ws)
    assert rows["Resistors"].id == uuid.UUID(created["id"])
    assert rows["Resistors"].archived_at is not None
    assert rows["Resistors"].default_symbol_ref is None
    # The rest of the tree still lands — one retired bucket is not a
    # reason to leave the workspace without capacitors.
    assert "Ceramic" in rows


def test_an_archived_root_stays_retired_wherever_it_was_filed(db, owned):
    """Root matching looks workspace-wide, so the archived lookup has to
    as well — otherwise a root the user archived under their own
    umbrella comes back as a new top-level category."""
    ws, client = owned
    passives = _create(client, "Passives")
    resistors = _create(client, "Resistors", parent_id=passives["id"])
    assert client.post(f"/api/categories/{resistors['id']}/archive").status_code == 200

    outcomes = _seed(db, ws)

    by_path = {o.path: o for o in outcomes}
    assert by_path["Resistors"].action == SKIPPED
    assert "archived" in by_path["Resistors"].detail
    rows = _categories(db, ws)
    assert rows["Resistors"].id == uuid.UUID(resistors["id"])
    assert rows["Resistors"].archived_at is not None


def test_a_skipped_parent_skips_its_children(db, owned):
    ws, client = owned
    _create(client, "Bulk caps", library_slug="capacitors")

    outcomes = _seed(db, ws)
    by_path = {o.path: o for o in outcomes}
    assert by_path["Capacitors"].action == SKIPPED
    for child in ("Ceramic", "Electrolytic", "Tantalum", "Film"):
        assert by_path[f"Capacitors / {child}"].action == SKIPPED
        assert "parent" in by_path[f"Capacitors / {child}"].detail


# ---------------------------------------------------------------------
# Audit + report
# ---------------------------------------------------------------------


def _audit_rows(db) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(AuditLog.action == AUDIT_ACTION)
        ).scalars()
    )


def test_apply_writes_one_audit_row_per_workspace(db, owned):
    ws, _client = owned

    _seed(db, ws)

    rows = _audit_rows(db)
    assert len(rows) == 1
    assert rows[0].workspace_id == ws.id
    assert rows[0].target_type == "part_category"
    assert len(rows[0].target_ids) == len(SEED_CATEGORIES)
    assert rows[0].comment == f"created={len(SEED_CATEGORIES)}"


def test_a_run_that_changes_nothing_writes_no_audit_row(db, owned):
    ws, _client = owned
    _seed(db, ws)
    _seed(db, ws)

    assert len(_audit_rows(db)) == 1


def test_the_report_is_csv_on_the_given_stream(db, owned):
    ws, _client = owned
    stream = io.StringIO()

    written = run_category_seed(db, apply=False, workspace_id=ws.id, stream=stream)

    lines = stream.getvalue().splitlines()
    assert lines[0] == ",".join(CSV_COLUMNS)
    assert len(lines) == len(SEED_CATEGORIES) + 1
    assert written == len(SEED_CATEGORIES)
    assert str(ws.id) in lines[1]


# ---------------------------------------------------------------------
# Losing the uniqueness race
# ---------------------------------------------------------------------


def _stale_index(monkeypatch, *, blind_to: Workspace) -> None:
    """Make the seed read a snapshot that is missing `blind_to`'s rows.

    The real race: `create_category` only takes the workspace-tree
    advisory lock when the new category has a parent, so a concurrent
    create of a *root* can take a name between this run's uniqueness
    check and its flush. Simulated here rather than threaded, because
    the outcome under test is what the job does with the IntegrityError,
    not the window that produces one.
    """
    real = seed_module._load_index

    def _patched(db, *, workspace_id):
        if workspace_id == blind_to.id:
            return seed_module._WorkspaceIndex([])
        return real(db, workspace_id=workspace_id)

    monkeypatch.setattr(seed_module, "_load_index", _patched)


def test_a_lost_race_costs_one_workspace_and_no_data(db, owned, monkeypatch):
    ws, client = owned
    existing = _create(client, "Resistors", description="mine")
    _stale_index(monkeypatch, blind_to=ws)

    outcomes = _seed(db, ws)

    assert [o.action for o in outcomes] == [SKIPPED]
    assert outcomes[0].detail == REASON_RACE
    rows = _categories(db, ws)
    assert list(rows) == ["Resistors"]
    assert rows["Resistors"].id == uuid.UUID(existing["id"])
    assert rows["Resistors"].description == "mine"
    assert _audit_rows(db) == []


def test_one_workspace_losing_the_race_does_not_stop_the_next(
    db, owned, other, monkeypatch
):
    mine, my_client = owned
    theirs, _their_client = other
    _create(my_client, "Resistors")
    _stale_index(monkeypatch, blind_to=mine)

    outcomes = seed_all_workspaces(db, apply=True)

    by_workspace = {o.workspace_id: o for o in outcomes if o.action == SKIPPED}
    assert by_workspace[mine.id].detail == REASON_RACE
    assert len(_categories(db, theirs)) == len(SEED_CATEGORIES)
    assert [row.workspace_id for row in _audit_rows(db)] == [theirs.id]


# ---------------------------------------------------------------------
# The CLI, end to end — this is the invocation an operator runs on prod
# ---------------------------------------------------------------------


def _rows_for(db, workspace_id: uuid.UUID) -> dict[str, PartCategory]:
    """Re-read by id. `run_job` ends by rolling back or committing and
    then closing the session, which expires whatever the caller held."""
    rows = db.execute(
        select(PartCategory).where(PartCategory.workspace_id == workspace_id)
    ).scalars()
    return {row.name: row for row in rows}


def test_the_cli_dry_run_prints_csv_and_writes_nothing(db, owned, capsys, tmp_path):
    ws, _client = owned
    workspace_id = ws.id

    code = run_job_main(
        ["category-seed", "--dry-run", "--workspace", str(workspace_id)],
        session_factory=lambda: db,
        heartbeat_dir=tmp_path,
    )

    assert code == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == ",".join(CSV_COLUMNS)
    assert len(out) == len(SEED_CATEGORIES) + 1
    assert _rows_for(db, workspace_id) == {}


def test_the_cli_apply_creates_the_tree(db, owned, capsys, tmp_path):
    ws, _client = owned
    workspace_id = ws.id

    code = run_job_main(
        ["category-seed", "--apply", "--workspace", str(workspace_id)],
        session_factory=lambda: db,
        heartbeat_dir=tmp_path,
    )

    assert code == 0
    assert capsys.readouterr().out.count(CREATED) == len(SEED_CATEGORIES)
    rows = _rows_for(db, workspace_id)
    assert len(rows) == len(SEED_CATEGORIES)
    assert rows["Resistors"].default_symbol_ref == "Device:R"


def test_every_seed_path_resolves_to_a_spec_schema_slug():
    unresolved = [
        path
        for path in iter_seed_paths()
        if category_slug_for(path) is None
        and path not in ("Capacitors", "Transistors")
    ]
    assert unresolved == []


def test_seedable_fields_never_include_identity_columns():
    assert "name" not in SEEDABLE_FIELDS
    assert "parent_id" not in SEEDABLE_FIELDS
    assert "library_slug" not in SEEDABLE_FIELDS
    assert "sort_order" not in SEEDABLE_FIELDS


# ---------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------


def test_seeding_one_workspace_leaves_the_other_untouched(db, owned, other):
    mine, _mine_client = owned
    theirs, _their_client = other

    outcomes = _seed(db, mine)

    assert {o.workspace_id for o in outcomes} == {mine.id}
    assert _categories(db, theirs) == {}
    assert [row.workspace_id for row in _audit_rows(db)] == [mine.id]


def test_seeding_all_workspaces_keeps_the_trees_separate(db, owned, other):
    mine, _mine_client = owned
    theirs, _their_client = other

    outcomes = seed_all_workspaces(db, apply=True)

    assert {o.workspace_id for o in outcomes} == {mine.id, theirs.id}
    for ws in (mine, theirs):
        rows = _categories(db, ws)
        assert len(rows) == len(SEED_CATEGORIES)
        assert rows["Ceramic"].parent_id == rows["Capacitors"].id
        assert all(row.workspace_id == ws.id for row in rows.values())


def test_a_name_taken_in_another_workspace_does_not_block_this_one(db, owned, other):
    mine, _mine_client = owned
    theirs, their_client = other
    _create(their_client, "Ceramic", library_slug="my-ceramic")

    _seed(db, mine)

    assert _categories(db, mine)["Ceramic"].parent_id == _categories(db, mine)[
        "Capacitors"
    ].id
    assert _categories(db, theirs)["Ceramic"].parent_id is None
