"""`/api/eda` libraries + `/api/parts/{id}/eda` config.

Covers the upload validation lane, CRUD with archive/restore conflicts,
footprint↔3D-model links, the per-part configuration, the file-serving
route, and the audit trail. Isolation coverage mirrors
`test_categories.py`: a second signup gets a second workspace, and every
cross-workspace reference must come back 404 rather than 403
(workspace-isolation invariant, ADR-0002).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.audit.models import AuditLog
from app.main import app
from tests._factories import create_part, signup_user

# ---------------------------------------------------------------------
# Fixture content
# ---------------------------------------------------------------------

STEP_BYTES = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
WRL_BYTES = b"#VRML V2.0 utf8\nShape { geometry Box { size 1 1 1 } }\n"
SPICE_BYTES = b".subckt MYPART 1 2\nR1 1 2 10k\n.ends\n"


def _symbol_text(name: str = "R", value: str = "R") -> str:
    return (
        f'(symbol "{name}" (in_bom yes) (on_board yes)\n'
        f'  (property "Reference" "R" (at 0 0 0))\n'
        f'  (property "Value" "{value}" (at 0 0 0))\n'
        f")\n"
    )


def _symbol_lib_text(*names: str) -> str:
    body = "\n".join(_symbol_text(n) for n in names)
    return f"(kicad_symbol_lib (version 20211014) (generator test)\n{body}\n)\n"


def _footprint_text(name: str = "R_0402", descr: str = "test") -> str:
    return (
        f'(footprint "{name}" (layer "F.Cu")\n'
        f'  (descr "{descr}")\n'
        f'  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))\n'
        f")\n"
    )


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


@pytest.fixture
def other_client(db):
    """A second workspace, for the cross-workspace probes."""
    c = TestClient(app)
    signup_user(c)
    return c


def _upload(client, path: str, filename: str, content, **form):
    if isinstance(content, str):
        content = content.encode("utf-8")
    data = {k: str(v) for k, v in form.items() if v is not None}
    return client.post(
        path,
        files={"file": (filename, content, "application/octet-stream")},
        data=data,
    )


def _upload_symbol(client, entry: str = "R", **form):
    """Upload a symbol whose in-file entry name is `entry`. A `name=`
    kwarg goes through as the form override, so the two are separable."""
    return _upload(
        client, "/api/eda/symbols", f"{entry}.kicad_sym", _symbol_text(entry), **form
    )


def _upload_footprint(client, entry: str = "R_0402", **form):
    return _upload(
        client, "/api/eda/footprints", f"{entry}.kicad_mod", _footprint_text(entry), **form
    )


def _upload_datafile(client, filename: str = "model.step", content: bytes = STEP_BYTES, **form):
    return _upload(client, "/api/eda/datafiles", filename, content, **form)


def _created(response, expected: int = 201) -> dict:
    assert response.status_code == expected, response.text
    return response.json()["data"]


def _audit_rows(db, action: str) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.action == action)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        ).scalars()
    )


# ---------------------------------------------------------------------
# Symbol uploads
# ---------------------------------------------------------------------


def test_upload_bare_symbol_takes_its_name_from_the_file(authed_client):
    data = _created(_upload_symbol(authed_client, "R"))
    assert data["name"] == "R"
    assert data["source"] == "manual"
    assert len(data["sha256"]) == 64
    assert data["size_bytes"] > 0
    assert data["archived_at"] is None
    assert authed_client.get("/api/eda/symbols").json()["data"][0]["id"] == data["id"]


def test_upload_single_symbol_library_unwraps_to_the_bare_entry(authed_client):
    """A `.kicad_sym` holding one symbol is the normal export shape. It
    stores as the bare entry, so a later phase can concatenate hosted
    symbols into a library without re-parsing each one."""
    data = _created(
        _upload(authed_client, "/api/eda/symbols", "lib.kicad_sym", _symbol_lib_text("R"))
    )
    assert data["name"] == "R"

    served = authed_client.get(f"/api/eda/files/{_ws_id(authed_client)}/{data['sha256']}.kicad_sym")
    assert served.status_code == 200, served.text
    assert served.text.startswith('(symbol "R"')


def test_name_form_field_overrides_the_parsed_name(authed_client):
    data = _created(_upload_symbol(authed_client, "R", name="Resistor_Generic"))
    assert data["name"] == "Resistor_Generic"


def test_multi_symbol_library_is_rejected_with_the_names_it_found(authed_client):
    r = _upload(
        authed_client,
        "/api/eda/symbols",
        "lib.kicad_sym",
        _symbol_lib_text("R", "C", "L"),
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "eda.multiple_symbols"
    assert body["symbol_count"] == 3
    assert body["symbol_names"] == ["R", "C", "L"]
    assert authed_client.get("/api/eda/symbols").json()["data"] == []


def test_reuploading_identical_bytes_returns_the_same_row_with_200(authed_client):
    first = _created(_upload_symbol(authed_client, "R"))
    again = _upload_symbol(authed_client, "R")
    assert again.status_code == 200, again.text
    assert again.json()["data"]["id"] == first["id"]
    assert len(authed_client.get("/api/eda/symbols").json()["data"]) == 1


def test_same_name_with_different_bytes_conflicts(authed_client):
    first = _created(_upload_symbol(authed_client, "R"))
    r = _upload(
        authed_client, "/api/eda/symbols", "R.kicad_sym", _symbol_text("R", value="10k")
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "eda.name_conflict"
    assert r.json()["existing_id"] == first["id"]
    assert r.json()["existing_name"] == "R"


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("not an s-expression", b"this is not a kicad file"),
        ("unbalanced", b'(symbol "R"'),
        ("wrong root", _footprint_text().encode()),
        ("nul bytes", b'(symbol "R\x00")'),
        ("invalid utf-8", b'(symbol "\xff\xfe")'),
        ("empty library", b"(kicad_symbol_lib (version 1))"),
    ],
)
def test_junk_symbol_uploads_are_422(authed_client, label, content):
    r = _upload(authed_client, "/api/eda/symbols", "bad.kicad_sym", content)
    assert r.status_code == 422, f"{label}: {r.text}"
    assert r.json()["code"] in ("eda.invalid_file", "eda.multiple_symbols")


def test_empty_upload_is_rejected(authed_client):
    r = _upload(authed_client, "/api/eda/symbols", "empty.kicad_sym", b"")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.empty_file"


def test_oversized_symbol_is_413_before_any_parsing(authed_client):
    """The cap is checked on the read, not after — a 2 MiB file must not
    cost us 2 MiB of s-expression parsing to reject."""
    padding = '(pad "x")\n' * 120_000  # comfortably past the 1 MiB symbol cap
    r = _upload(
        authed_client, "/api/eda/symbols", "huge.kicad_sym", f'(symbol "R"\n{padding})'
    )
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "eda.file_too_large"


# ---------------------------------------------------------------------
# Footprint uploads
# ---------------------------------------------------------------------


def test_upload_footprint(authed_client):
    data = _created(_upload_footprint(authed_client, "R_0402_1005Metric"))
    assert data["name"] == "R_0402_1005Metric"
    assert authed_client.get("/api/eda/footprints").json()["data"][0]["id"] == data["id"]


def test_legacy_module_root_is_accepted(authed_client):
    """`(module …)` is the pre-6.0 spelling; files exported by older
    tools still use it and parse identically."""
    data = _created(
        _upload(
            authed_client,
            "/api/eda/footprints",
            "old.kicad_mod",
            '(module "R_0402" (layer "F.Cu"))',
        )
    )
    assert data["name"] == "R_0402"


def test_symbol_uploaded_to_the_footprint_endpoint_is_422(authed_client):
    r = _upload(authed_client, "/api/eda/footprints", "R.kicad_mod", _symbol_text("R"))
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.invalid_file"


# ---------------------------------------------------------------------
# Data-file uploads
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "content", "kind"),
    [
        ("part.step", STEP_BYTES, "step"),
        ("part.stp", STEP_BYTES, "step"),
        ("part.wrl", WRL_BYTES, "wrl"),
        ("part.lib", SPICE_BYTES, "spice"),
        ("part.sub", SPICE_BYTES, "spice"),
        ("part.cir", SPICE_BYTES, "spice"),
        ("part.spice", SPICE_BYTES, "spice"),
    ],
)
def test_datafile_kind_comes_from_the_extension(authed_client, filename, content, kind):
    data = _created(_upload_datafile(authed_client, filename, content))
    assert data["kind"] == kind
    assert data["name"] == filename


def test_datafile_with_an_unknown_extension_is_422(authed_client):
    r = _upload_datafile(authed_client, "part.txt", b"whatever")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.unsupported_kind"


@pytest.mark.parametrize(
    ("filename", "content"),
    [("part.step", b"not a step file"), ("part.wrl", b"not a vrml file")],
)
def test_3d_model_without_its_signature_is_422(authed_client, filename, content):
    r = _upload_datafile(authed_client, filename, content)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.invalid_file"


def test_binary_spice_upload_is_rejected(authed_client):
    r = _upload_datafile(authed_client, "part.lib", b".subckt\x00\x01\x02")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.invalid_file"


def test_datafile_name_strips_any_client_supplied_path(authed_client):
    """The filename becomes a display name, never part of a path — files
    are stored under their content hash — but it must not carry a
    traversal fragment into the row either."""
    data = _created(_upload_datafile(authed_client, "../../etc/passwd.step", STEP_BYTES))
    assert data["name"] == "passwd.step"


def test_same_name_different_kind_is_not_a_conflict(authed_client):
    """`kind` is part of the unique key, so a part can host both a STEP
    model and a SPICE model under one name."""
    step = _created(_upload_datafile(authed_client, "part.step", STEP_BYTES, name="MYPART"))
    spice = _created(_upload_datafile(authed_client, "part.lib", SPICE_BYTES, name="MYPART"))
    assert step["id"] != spice["id"]
    assert {step["kind"], spice["kind"]} == {"step", "spice"}


# ---------------------------------------------------------------------
# CRUD walk: rename, archive, restore
# ---------------------------------------------------------------------


def test_symbol_crud_walk(authed_client):
    c = authed_client
    assert c.get("/api/eda/symbols").json()["data"] == []
    created = _created(_upload_symbol(c, "R"))

    r = c.patch(f"/api/eda/symbols/{created['id']}", json={"name": "R_Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["name"] == "R_Renamed"
    # A rename rewrites the stored file so the entry name inside the
    # bytes matches the row — content-addressing moves the sha with it
    # (see test_rename_rewrites_the_stored_file).
    assert r.json()["data"]["sha256"] != created["sha256"]

    assert c.post(f"/api/eda/symbols/{created['id']}/archive").status_code == 200
    assert c.get("/api/eda/symbols").json()["data"] == []
    archived = c.get("/api/eda/symbols?include_archived=true").json()["data"]
    assert [row["id"] for row in archived] == [created["id"]]
    assert archived[0]["archived_at"] is not None

    assert c.post(f"/api/eda/symbols/{created['id']}/restore").status_code == 200
    assert [row["id"] for row in c.get("/api/eda/symbols").json()["data"]] == [created["id"]]


def test_archived_name_is_free_for_reuse(authed_client):
    c = authed_client
    first = _created(_upload_symbol(c, "R"))
    assert c.post(f"/api/eda/symbols/{first['id']}/archive").status_code == 200

    second = _created(_upload(c, "/api/eda/symbols", "R.kicad_sym", _symbol_text("R", "10k")))
    assert second["id"] != first["id"]


def test_restore_conflicts_when_the_name_was_taken(authed_client):
    c = authed_client
    first = _created(_upload_symbol(c, "R"))
    assert c.post(f"/api/eda/symbols/{first['id']}/archive").status_code == 200
    second = _created(_upload(c, "/api/eda/symbols", "R.kicad_sym", _symbol_text("R", "10k")))

    r = c.post(f"/api/eda/symbols/{first['id']}/restore")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "eda.name_conflict"
    assert r.json()["existing_id"] == second["id"]


def test_rename_onto_another_active_name_conflicts(authed_client):
    c = authed_client
    first = _created(_upload_symbol(c, "R"))
    second = _created(_upload_symbol(c, "C"))

    r = c.patch(f"/api/eda/symbols/{second['id']}", json={"name": "R"})
    assert r.status_code == 409, r.text
    assert r.json()["existing_id"] == first["id"]


def test_rename_to_own_name_is_a_noop_not_a_conflict(authed_client):
    created = _created(_upload_symbol(authed_client, "R"))
    r = authed_client.patch(f"/api/eda/symbols/{created['id']}", json={"name": "R"})
    assert r.status_code == 200, r.text


def test_patch_null_name_is_422(authed_client):
    created = _created(_upload_symbol(authed_client, "R"))
    r = authed_client.patch(f"/api/eda/symbols/{created['id']}", json={"name": None})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.field_not_nullable"


def test_patch_rejects_unknown_field(authed_client):
    created = _created(_upload_symbol(authed_client, "R"))
    r = authed_client.patch(f"/api/eda/symbols/{created['id']}", json={"banana": "yellow"})
    assert r.status_code == 422, r.text
    assert "banana" in r.text


@pytest.mark.parametrize(
    ("area", "code"),
    [
        ("symbols", "eda_symbol.not_found"),
        ("footprints", "eda_footprint.not_found"),
        ("datafiles", "eda_datafile.not_found"),
    ],
)
def test_unknown_id_is_a_domain_specific_404(authed_client, area, code):
    r = authed_client.post(f"/api/eda/{area}/{uuid.uuid4()}/archive")
    assert r.status_code == 404, r.text
    assert r.json()["code"] == code


def test_datafile_and_footprint_archive_restore(authed_client):
    """The three library types share one implementation; this pins that
    the other two are actually wired to it."""
    c = authed_client
    footprint = _created(_upload_footprint(c, "R_0402"))
    datafile = _created(_upload_datafile(c, "part.step", STEP_BYTES))

    for area, row in (("footprints", footprint), ("datafiles", datafile)):
        assert c.post(f"/api/eda/{area}/{row['id']}/archive").status_code == 200
        assert c.get(f"/api/eda/{area}").json()["data"] == []
        assert c.post(f"/api/eda/{area}/{row['id']}/restore").status_code == 200
        assert len(c.get(f"/api/eda/{area}").json()["data"]) == 1


def test_list_limit_caps_results(authed_client):
    for name in ("R", "C", "L"):
        _created(_upload_symbol(authed_client, name))
    assert len(authed_client.get("/api/eda/symbols", params={"limit": 2}).json()["data"]) == 2


# ---------------------------------------------------------------------
# Categories on library entries
# ---------------------------------------------------------------------


def _create_category(client, name: str = "Resistors") -> dict:
    r = client.post("/api/categories", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_symbol_can_be_filed_under_a_category(authed_client):
    category = _create_category(authed_client)
    created = _created(_upload_symbol(authed_client, "R", category_id=category["id"]))
    assert created["category_id"] == category["id"]

    r = authed_client.patch(f"/api/eda/symbols/{created['id']}", json={"category_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["category_id"] is None


def test_symbol_rejects_a_foreign_workspace_category(authed_client, other_client):
    foreign = _create_category(other_client, "Theirs")
    r = _upload_symbol(authed_client, "R", category_id=foreign["id"])
    assert r.status_code == 404, r.text
    assert authed_client.get("/api/eda/symbols").json()["data"] == []


def test_symbol_rejects_an_archived_category_but_keeps_an_unchanged_one_patchable(
    authed_client,
):
    """Same change-aware guard as `parts.category_id`: the CAD tab
    round-trips the current category with every save, so refusing an
    unchanged (since-archived) value would brick the form."""
    c = authed_client
    category = _create_category(c, "Sunsetting")
    created = _created(_upload_symbol(c, "R", category_id=category["id"]))
    assert c.post(f"/api/categories/{category['id']}/archive").status_code == 200

    r = c.patch(
        f"/api/eda/symbols/{created['id']}",
        json={"category_id": category["id"], "name": "R_Still_Fine"},
    )
    assert r.status_code == 200, r.text

    other = _created(_upload_symbol(c, "C"))
    r = c.patch(f"/api/eda/symbols/{other['id']}", json={"category_id": category["id"]})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "category.archived"


# ---------------------------------------------------------------------
# Footprint ↔ 3D model links
# ---------------------------------------------------------------------


def test_link_and_unlink_a_3d_model(authed_client):
    c = authed_client
    footprint = _created(_upload_footprint(c, "R_0402"))
    step = _created(_upload_datafile(c, "part.step", STEP_BYTES))

    r = c.post(
        f"/api/eda/footprints/{footprint['id']}/models",
        json={"datafile_id": step["id"], "position": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == [{"datafile_id": step["id"], "position": 1}]
    assert (
        c.get(f"/api/eda/footprints/{footprint['id']}/models").json()["data"][0]["position"]
        == 1
    )

    r = c.delete(f"/api/eda/footprints/{footprint['id']}/models/{step['id']}")
    assert r.status_code == 200, r.text
    assert c.get(f"/api/eda/footprints/{footprint['id']}/models").json()["data"] == []


def test_relinking_the_same_pair_moves_it_rather_than_conflicting(authed_client):
    """DELETE-then-POST is what a client replay looks like; a 409 on the
    unique index would make a retried save fail."""
    c = authed_client
    footprint = _created(_upload_footprint(c, "R_0402"))
    step = _created(_upload_datafile(c, "part.step", STEP_BYTES))
    path = f"/api/eda/footprints/{footprint['id']}/models"

    assert c.post(path, json={"datafile_id": step["id"], "position": 0}).status_code == 200
    r = c.post(path, json={"datafile_id": step["id"], "position": 5})
    assert r.status_code == 200, r.text
    assert r.json()["data"] == [{"datafile_id": step["id"], "position": 5}]


def test_models_are_ordered_by_position(authed_client):
    c = authed_client
    footprint = _created(_upload_footprint(c, "R_0402"))
    step = _created(_upload_datafile(c, "part.step", STEP_BYTES))
    wrl = _created(_upload_datafile(c, "part.wrl", WRL_BYTES))
    path = f"/api/eda/footprints/{footprint['id']}/models"

    c.post(path, json={"datafile_id": step["id"], "position": 2})
    c.post(path, json={"datafile_id": wrl["id"], "position": 1})
    assert [row["datafile_id"] for row in c.get(path).json()["data"]] == [
        wrl["id"],
        step["id"],
    ]


def test_a_spice_model_cannot_be_attached_to_a_footprint(authed_client):
    c = authed_client
    footprint = _created(_upload_footprint(c, "R_0402"))
    spice = _created(_upload_datafile(c, "part.lib", SPICE_BYTES))

    r = c.post(
        f"/api/eda/footprints/{footprint['id']}/models",
        json={"datafile_id": spice["id"]},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.unsupported_kind"


def test_linking_a_foreign_workspace_datafile_is_404(authed_client, other_client):
    footprint = _created(_upload_footprint(authed_client, "R_0402"))
    foreign = _created(_upload_datafile(other_client, "theirs.step", STEP_BYTES))

    r = authed_client.post(
        f"/api/eda/footprints/{footprint['id']}/models",
        json={"datafile_id": foreign["id"]},
    )
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "eda_datafile.not_found"
    assert authed_client.get(f"/api/eda/footprints/{footprint['id']}/models").json()["data"] == []


def test_unlinking_a_pair_that_is_not_linked_succeeds(authed_client):
    """DELETE is idempotent — the caller's intent ("this pair is not
    linked") holds either way."""
    c = authed_client
    footprint = _created(_upload_footprint(c, "R_0402"))
    step = _created(_upload_datafile(c, "part.step", STEP_BYTES))
    r = c.delete(f"/api/eda/footprints/{footprint['id']}/models/{step['id']}")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------
# Per-part EDA config
# ---------------------------------------------------------------------


def test_part_without_a_config_returns_null(authed_client):
    part_id = create_part(authed_client, "Resistor")
    r = authed_client.get(f"/api/parts/{part_id}/eda")
    assert r.status_code == 200, r.text
    assert r.json()["data"] is None


def test_part_eda_put_get_delete_round_trip(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    symbol = _created(_upload_symbol(c, "R"))
    footprint = _created(_upload_footprint(c, "R_0402"))
    spice = _created(_upload_datafile(c, "part.lib", SPICE_BYTES))

    body = {
        "symbol_id": symbol["id"],
        "footprint_id": footprint["id"],
        "spice_datafile_id": spice["id"],
        "value": "10k",
        "keywords": "resistor smd",
        "footprint_filters": ["R_*", "*_0402_*"],
        "exclude_from_bom": True,
        "exclude_from_sim": False,
        "sim_device": "R",
        "sim_pins": "1=+ 2=-",
        "sim_params": "r=10k",
    }
    r = c.put(f"/api/parts/{part_id}/eda", json=body)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["part_id"] == part_id
    assert data["symbol_id"] == symbol["id"]
    assert data["footprint_filters"] == ["R_*", "*_0402_*"]
    assert data["exclude_from_bom"] is True
    assert data["exclude_from_board"] is False
    assert data["exclude_from_sim"] is False

    assert c.get(f"/api/parts/{part_id}/eda").json()["data"]["value"] == "10k"

    assert c.delete(f"/api/parts/{part_id}/eda").status_code == 200
    assert c.get(f"/api/parts/{part_id}/eda").json()["data"] is None


def test_put_is_a_full_replacement_not_a_merge(authed_client):
    """PUT semantics, and what the CAD tab relies on: the form posts every
    field on every save, so an omitted field means "not set" — that is the
    only way "clear the symbol" is expressible."""
    c = authed_client
    part_id = create_part(c, "Resistor")
    symbol = _created(_upload_symbol(c, "R"))

    c.put(f"/api/parts/{part_id}/eda", json={"symbol_id": symbol["id"], "value": "10k"})
    r = c.put(f"/api/parts/{part_id}/eda", json={"value": "22k"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["value"] == "22k"
    assert data["symbol_id"] is None
    assert data["exclude_from_sim"] is True  # back to the column default


def test_put_updates_in_place_rather_than_creating_a_second_row(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    c.put(f"/api/parts/{part_id}/eda", json={"value": "10k"})
    c.put(f"/api/parts/{part_id}/eda", json={"value": "22k"})
    # UNIQUE(part_id) would have raised; this asserts the read side too.
    assert c.get(f"/api/parts/{part_id}/eda").json()["data"]["value"] == "22k"


@pytest.mark.real_db
def test_concurrent_first_saves_do_not_500_on_the_unique_index():
    """Two saves for a part with no config yet both find nothing and race
    to INSERT; `uq_part_eda_part` lets one through. The loser must recover
    onto the winning row (last-writer-wins), not surface a 500.

    real_db: the two requests run on separate connections, so the first
    insert has to be genuinely committed for the second to collide with it
    — under the savepoint fixture they'd share one transaction and never
    race at all.
    """
    from concurrent.futures import ThreadPoolExecutor

    a = TestClient(app)
    signup_user(a)
    part_id = create_part(a, "Contended")

    def save(value: str):
        client = TestClient(app)
        client.cookies = a.cookies
        return client.put(f"/api/parts/{part_id}/eda", json={"value": value})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result() for f in [pool.submit(save, "10k"), pool.submit(save, "22k")]]

    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    # Exactly one row survived, holding one of the two values.
    assert a.get(f"/api/parts/{part_id}/eda").json()["data"]["value"] in ("10k", "22k")


def test_external_ref_is_stored_as_given(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    r = c.put(
        f"/api/parts/{part_id}/eda",
        json={"symbol_ref_external": "Device:R", "footprint_ref_external": "Resistor_SMD:R_0402"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["symbol_ref_external"] == "Device:R"
    assert r.json()["data"]["symbol_id"] is None


@pytest.mark.parametrize("slot", ["symbol", "footprint"])
def test_setting_both_a_hosted_and_an_external_ref_is_422(authed_client, slot):
    c = authed_client
    part_id = create_part(c, "Resistor")
    entry = (
        _created(_upload_symbol(c, "R"))
        if slot == "symbol"
        else _created(_upload_footprint(c, "R_0402"))
    )

    r = c.put(
        f"/api/parts/{part_id}/eda",
        json={f"{slot}_id": entry["id"], f"{slot}_ref_external": "Device:R"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.ref_conflict"
    assert r.json()["slot"] == slot
    assert c.get(f"/api/parts/{part_id}/eda").json()["data"] is None


@pytest.mark.parametrize("slot", ["symbol", "footprint"])
def test_archived_library_entry_cannot_be_referenced(authed_client, slot):
    c = authed_client
    part_id = create_part(c, "Resistor")
    area = "symbols" if slot == "symbol" else "footprints"
    entry = (
        _created(_upload_symbol(c, "R"))
        if slot == "symbol"
        else _created(_upload_footprint(c, "R_0402"))
    )
    assert c.post(f"/api/eda/{area}/{entry['id']}/archive").status_code == 200

    r = c.put(f"/api/parts/{part_id}/eda", json={f"{slot}_id": entry["id"]})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "eda.archived"


def test_spice_slot_rejects_a_3d_model(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    step = _created(_upload_datafile(c, "part.step", STEP_BYTES))

    r = c.put(f"/api/parts/{part_id}/eda", json={"spice_datafile_id": step["id"]})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.unsupported_kind"


@pytest.mark.parametrize(
    ("field", "area"),
    [
        ("symbol_id", "symbols"),
        ("footprint_id", "footprints"),
        ("spice_datafile_id", "datafiles"),
    ],
)
def test_unknown_reference_ids_are_404(authed_client, field, area):
    part_id = create_part(authed_client, "Resistor")
    r = authed_client.put(f"/api/parts/{part_id}/eda", json={field: str(uuid.uuid4())})
    assert r.status_code == 404, r.text


def test_part_eda_rejects_unknown_field(authed_client):
    part_id = create_part(authed_client, "Resistor")
    r = authed_client.put(f"/api/parts/{part_id}/eda", json={"banana": "yellow"})
    assert r.status_code == 422, r.text
    assert "banana" in r.text


def test_part_eda_on_an_unknown_part_is_404(authed_client):
    r = authed_client.put(f"/api/parts/{uuid.uuid4()}/eda", json={"value": "10k"})
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "part.not_found"


def test_deleting_a_config_that_does_not_exist_succeeds(authed_client):
    part_id = create_part(authed_client, "Resistor")
    assert authed_client.delete(f"/api/parts/{part_id}/eda").status_code == 200


# ---------------------------------------------------------------------
# File serving
# ---------------------------------------------------------------------


def _ws_id(client) -> str:
    """The client's own workspace id — a signup creates exactly one."""
    return client.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]


def test_stored_file_is_served_back_as_a_forced_download(authed_client):
    data = _created(_upload_symbol(authed_client, "R"))
    ws_id = _ws_id(authed_client)

    r = authed_client.get(f"/api/eda/files/{ws_id}/{data['sha256']}.kicad_sym")
    assert r.status_code == 200, r.text
    assert r.text.startswith('(symbol "R"')
    # Attacker-supplied text on our own origin: never inline, never sniffed.
    assert r.headers["content-disposition"] == "attachment"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"] == "application/octet-stream"
    assert "immutable" in r.headers["cache-control"]


def test_save_as_name_is_sanitised_and_keeps_the_extension(authed_client):
    data = _created(_upload_symbol(authed_client, "R"))
    ws_id = _ws_id(authed_client)

    r = authed_client.get(
        f"/api/eda/files/{ws_id}/{data['sha256']}.kicad_sym",
        params={"name": 'evil"; rm -rf /.kicad_sym'},
    )
    assert r.status_code == 200, r.text
    disposition = r.headers["content-disposition"]
    assert '"' not in disposition.split("filename=")[1].strip('"')
    assert disposition.endswith('.kicad_sym"')


def test_file_from_another_workspace_is_404(authed_client, other_client):
    theirs = _created(_upload_symbol(other_client, "R"))
    their_ws = _ws_id(other_client)

    r = authed_client.get(f"/api/eda/files/{their_ws}/{theirs['sha256']}.kicad_sym")
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "eda.file_not_found"


def test_missing_file_is_404(authed_client):
    ws_id = _ws_id(authed_client)
    r = authed_client.get(f"/api/eda/files/{ws_id}/{'0' * 64}.kicad_sym")
    assert r.status_code == 404, r.text


@pytest.mark.parametrize("filename", [".env", ".ssh", ".gitconfig"])
def test_dotted_filenames_are_refused(authed_client, filename):
    """Path traversal guard — the stored name is always a flat
    content-addressed `<sha>.<ext>`, so a leading dot is never legitimate."""
    ws_id = _ws_id(authed_client)
    r = authed_client.get(f"/api/eda/files/{ws_id}/{filename}")
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "eda.invalid_filename"


@pytest.mark.parametrize("filename", ["..", "../../etc/passwd", "%2e%2e%2f%2e%2e%2fetc%2fpasswd"])
def test_traversal_attempts_never_reach_the_filesystem(authed_client, filename):
    """A literal `..` segment is collapsed by URL normalisation and never
    routes; the encoded and multi-segment forms miss the single-segment
    `{filename}` path parameter. Either way the response is a refusal and
    no file leaves the workspace directory — this asserts the outcome
    rather than which layer produced it."""
    ws_id = _ws_id(authed_client)
    r = authed_client.get(f"/api/eda/files/{ws_id}/{filename}")
    assert r.status_code in (400, 404), r.text


# ---------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------


def test_library_entries_are_isolated_per_workspace(authed_client, other_client):
    mine = _created(_upload_symbol(authed_client, "R"))

    assert other_client.get("/api/eda/symbols").json()["data"] == []
    for path, method, kwargs in (
        (f"/api/eda/symbols/{mine['id']}", "patch", {"json": {"name": "Hijacked"}}),
        (f"/api/eda/symbols/{mine['id']}/archive", "post", {}),
        (f"/api/eda/symbols/{mine['id']}/restore", "post", {}),
    ):
        r = getattr(other_client, method)(path, **kwargs)
        assert r.status_code == 404, f"{method} {path}: {r.text}"
        assert r.json()["code"] == "eda_symbol.not_found"


def test_name_conflict_precheck_is_workspace_scoped(authed_client, other_client):
    """The one line in the feature that fails open: drop the
    `workspace_id` filter from `service._active_by_name` and every other
    test still passes, because the isolation test above 404s at
    `get_entry` before any conflict probe runs. Upload would silently
    become a cross-workspace name oracle — a 409 carrying another
    tenant's `existing_id` and `existing_name`."""
    mine = _created(_upload_symbol(authed_client, "R"))

    # The same name in a second workspace is not a collision at all.
    theirs = _created(_upload_symbol(other_client, "R"))
    assert theirs["id"] != mine["id"]

    # And a rename that does collide names the *local* row, never mine.
    second = _created(_upload_symbol(other_client, "C"))
    r = other_client.patch(f"/api/eda/symbols/{second['id']}", json={"name": "R"})
    assert r.status_code == 409, r.text
    assert r.json()["existing_id"] == theirs["id"]
    assert r.json()["existing_id"] != mine["id"]


def test_part_eda_is_isolated_per_workspace(authed_client, other_client):
    part_id = create_part(authed_client, "Mine")
    authed_client.put(f"/api/parts/{part_id}/eda", json={"value": "10k"})

    assert other_client.get(f"/api/parts/{part_id}/eda").status_code == 404
    assert other_client.put(f"/api/parts/{part_id}/eda", json={"value": "x"}).status_code == 404
    assert other_client.delete(f"/api/parts/{part_id}/eda").status_code == 404
    # The write was refused wholesale, not partially applied.
    assert authed_client.get(f"/api/parts/{part_id}/eda").json()["data"]["value"] == "10k"


def test_part_eda_cannot_reference_a_foreign_workspace_entry(authed_client, other_client):
    part_id = create_part(authed_client, "Mine")
    foreign = _created(_upload_symbol(other_client, "R"))

    r = authed_client.put(f"/api/parts/{part_id}/eda", json={"symbol_id": foreign["id"]})
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "eda_symbol.not_found"
    assert authed_client.get(f"/api/parts/{part_id}/eda").json()["data"] is None


# ---------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------


def test_each_mutation_writes_one_audit_row(authed_client, db):
    c = authed_client
    created = _created(_upload_symbol(c, "R"))

    rows = _audit_rows(db, "eda_symbol.uploaded")
    assert len(rows) == 1
    assert rows[0].target_type == "eda_symbol"
    assert rows[0].target_ids == [uuid.UUID(created["id"])]
    assert rows[0].comment == f"sha256={created['sha256']}"
    assert rows[0].workspace_id is not None
    assert rows[0].user_id is not None

    c.patch(f"/api/eda/symbols/{created['id']}", json={"name": "R2"})
    rows = _audit_rows(db, "eda_symbol.updated")
    assert len(rows) == 1
    assert rows[0].comment == "fields=name"

    c.post(f"/api/eda/symbols/{created['id']}/archive")
    assert len(_audit_rows(db, "eda_symbol.archived")) == 1
    c.post(f"/api/eda/symbols/{created['id']}/restore")
    assert len(_audit_rows(db, "eda_symbol.restored")) == 1


def test_part_eda_mutations_are_audited_against_the_part(authed_client, db):
    """The config row is deleted and recreated freely, so its own id is
    ephemeral — the trail is keyed on the part an auditor would search
    for."""
    c = authed_client
    part_id = create_part(c, "Resistor")

    c.put(f"/api/parts/{part_id}/eda", json={"value": "10k", "keywords": "res"})
    rows = _audit_rows(db, "part_eda.updated")
    assert len(rows) == 1
    assert rows[0].target_type == "part_eda"
    assert rows[0].target_ids == [uuid.UUID(part_id)]
    assert rows[0].comment == "fields=keywords,value"

    c.delete(f"/api/parts/{part_id}/eda")
    rows = _audit_rows(db, "part_eda.deleted")
    assert len(rows) == 1
    assert rows[0].target_ids == [uuid.UUID(part_id)]


def test_model_link_and_unlink_are_audited(authed_client, db):
    c = authed_client
    footprint = _created(_upload_footprint(c, "R_0402"))
    step = _created(_upload_datafile(c, "part.step", STEP_BYTES))
    path = f"/api/eda/footprints/{footprint['id']}/models"

    c.post(path, json={"datafile_id": step["id"]})
    c.delete(f"{path}/{step['id']}")

    rows = _audit_rows(db, "eda_footprint.updated")
    assert [row.comment for row in rows] == [
        f"model_unlinked={step['id']}",
        f"model_linked={step['id']}",
    ]
    assert all(row.target_ids == [uuid.UUID(footprint["id"])] for row in rows)


def test_a_deduplicated_upload_writes_no_second_audit_row(authed_client, db):
    """Re-uploading identical bytes changes nothing, so it records
    nothing — an audit trail of no-ops is noise."""
    c = authed_client
    _created(_upload_symbol(c, "R"))
    assert len(_audit_rows(db, "eda_symbol.uploaded")) == 1

    assert _upload_symbol(c, "R").status_code == 200
    assert len(_audit_rows(db, "eda_symbol.uploaded")) == 1


def test_failed_mutation_writes_no_audit_row(authed_client, db):
    c = authed_client
    _created(_upload_symbol(c, "R"))
    before = len(_audit_rows(db, "eda_symbol.uploaded"))

    r = _upload(c, "/api/eda/symbols", "R.kicad_sym", _symbol_text("R", "10k"))
    assert r.status_code == 409
    assert len(_audit_rows(db, "eda_symbol.uploaded")) == before


# ---------------------------------------------------------------------
# P2 review fixes
# ---------------------------------------------------------------------


def test_rename_rewrites_the_stored_file(authed_client):
    """The row's name and the entry name inside the stored bytes must
    agree — phase 5 resolves LibNick:Entry against the file content."""
    c = authed_client
    created = _created(_upload_symbol(c, "OldName"))

    r = c.patch(f"/api/eda/symbols/{created['id']}", json={"name": "NewName"})
    assert r.status_code == 200, r.text
    updated = r.json()["data"]
    assert updated["sha256"] != created["sha256"]

    served = c.get(f"/api/eda/files/{_ws_id(c)}/{updated['sha256']}.kicad_sym")
    assert served.status_code == 200, served.text
    body = served.text
    assert '"NewName"' in body
    assert '"OldName"' not in body


def test_non_ascii_save_as_name_does_not_500(authed_client):
    c = authed_client
    data = _created(_upload_symbol(c, "R"))
    r = c.get(
        f"/api/eda/files/{_ws_id(c)}/{data['sha256']}.kicad_sym",
        params={"name": "Файл-Схемы"},
    )
    assert r.status_code == 200, r.text
    disposition = r.headers["content-disposition"]
    assert disposition.isascii()
    assert "attachment" in disposition


def test_dedupe_reupload_with_bad_category_is_404(authed_client):
    """An invalid category_id must 404 on every upload path — including
    the identical-bytes dedupe path, which previously skipped validation."""
    c = authed_client
    _created(_upload_symbol(c, "R"))
    r = _upload_symbol(c, "R", category_id=str(uuid.uuid4()))
    assert r.status_code == 404, r.text


def test_part_eda_delete_then_recreate(authed_client):
    """uq_part_eda_part is a plain UNIQUE (not partial on archived_at) —
    safe only while delete hard-deletes. Pin that a part can always get a
    fresh config after a delete."""
    c = authed_client
    part_id = create_part(c, "Recreatable")
    assert c.put(f"/api/parts/{part_id}/eda", json={"value": "1k"}).status_code == 200
    assert c.delete(f"/api/parts/{part_id}/eda").status_code == 200
    r = c.put(f"/api/parts/{part_id}/eda", json={"value": "2k"})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/parts/{part_id}/eda").json()["data"]["value"] == "2k"


# ---------------------------------------------------------------------
# P2 security review fixes
# ---------------------------------------------------------------------


def test_amplified_canonical_form_is_rejected(authed_client):
    """Deep-and-wide input whose RE-EMITTED form exceeds the kind cap is
    a 422, not a 201 that writes ~200x the upload to disk (sec HIGH-1)."""
    deep_open = "(x " * 25
    leaves = "(p 1)" * 20000
    text = '(symbol "Amp" ' + deep_open + leaves + ")" * 25 + ")"
    assert len(text.encode()) < 1024 * 1024  # input passes the upload cap

    r = _upload(authed_client, "/api/eda/symbols", "amp.kicad_sym", text)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.file_too_large"


def test_overlong_parsed_entry_name_is_422_not_500(authed_client):
    """The form-supplied name is capped by the route; the PARSED fallback
    must be capped too or Postgres raises a DataError 500 (sec HIGH-2)."""
    r = _upload(
        authed_client,
        "/api/eda/symbols",
        "long.kicad_sym",
        _symbol_text("N" * 250),
    )
    assert r.status_code == 422, r.text


def test_multi_symbol_error_truncates_echoed_names(authed_client):
    long_a = "A" * 500
    long_b = "B" * 500
    lib = (
        f'(kicad_symbol_lib (version 20211014) (generator test) '
        f'(symbol "{long_a}" (pin_names)) (symbol "{long_b}" (pin_names)))'
    )
    r = _upload(authed_client, "/api/eda/symbols", "multi.kicad_sym", lib)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "eda.multiple_symbols"
    assert body["symbol_names"]
    assert all(len(n) <= 80 for n in body["symbol_names"])


def test_rejected_upload_writes_no_orphan_blob(authed_client):
    """A 409 name conflict must leave nothing on disk — the blob is
    written only after the row insert succeeds (sec MEDIUM-2)."""
    import os

    from app.domain.eda import storage as eda_storage

    _created(_upload_symbol(authed_client, "R"))

    conflicting = _symbol_text("R", value="10k")
    _, canonical = eda_storage.canonical_symbol(conflicting.encode())
    sha, _size = eda_storage.digest(canonical)

    r = _upload(authed_client, "/api/eda/symbols", "R.kicad_sym", conflicting)
    assert r.status_code == 409, r.text
    orphan = eda_storage.path_for(_ws_id(authed_client), f"{sha}.kicad_sym")
    assert not os.path.exists(orphan)
