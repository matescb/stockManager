"""Vendor imports — zips, the library importer, and the LCSC fetch.

Covers vendor detection across the three archive layouts, the
`(model …)` path rewrite that points a vendor footprint at our own
storage, part wiring (fill-vs-overwrite), the dedupe and name-conflict
rules that let one bad member cost only itself, the archive guards, and
the audit trail. Nothing here touches the network: the LCSC tests
monkeypatch the `easyeda2kicad` classes `domain/eda/lcsc.py` drives, so
the conversion glue is exercised for real and only the HTTP calls are
faked.
"""
from __future__ import annotations

import inspect
import io
import os
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.audit.models import AuditLog
from app.domain.eda import storage as eda_storage
from app.domain.eda import vendor_zip
from app.main import app
from tests._factories import create_part, signup_user
from tests.test_eda import (
    SPICE_BYTES,
    STEP_BYTES,
    WRL_BYTES,
    _footprint_text,
    _symbol_text,
    _ws_id,
)

# ---------------------------------------------------------------------
# Fixture content
# ---------------------------------------------------------------------

LEGACY_LIB_BYTES = b"EESchema-LIBRARY Version 2.4\n#\nDEF R R 0 0 N Y 1 F N\nENDDEF\n"


def _footprint_with_models(name: str = "R_0402", *model_paths: str) -> str:
    """A footprint carrying `(model …)` nodes, the way a vendor ships it —
    absolute or `${KIPRJMOD}`-relative paths into the vendor's own tree."""
    models = "\n".join(
        f'  (model "{path}" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))'
        for path in model_paths
    )
    return (
        f'(footprint "{name}" (layer "F.Cu")\n'
        f'  (descr "test")\n'
        f'  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))\n'
        f"{models}\n"
        f")\n"
    )


def _symbol_with_footprint(name: str, footprint_ref: str) -> str:
    return (
        f'(symbol "{name}" (in_bom yes) (on_board yes)\n'
        f'  (property "Reference" "U" (at 0 0 0))\n'
        f'  (property "Value" "{name}" (at 0 0 0))\n'
        f'  (property "Footprint" "{footprint_ref}" (at 0 0 0))\n'
        f")\n"
    )


def _symbol_lib(*names: str) -> str:
    return _wrap_lib(*(_symbol_text(n) for n in names))


def _wrap_lib(*symbol_texts: str) -> str:
    """Wrap bare `(symbol …)` entries in a library root. Two bare symbols
    concatenated are two top-level expressions, which is not a file."""
    body = "\n".join(symbol_texts)
    return f"(kicad_symbol_lib (version 20211014) (generator test)\n{body}\n)\n"


def _zip_bytes(members: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content if isinstance(content, bytes) else content.encode())
    return buf.getvalue()


def _snapeda_zip(part: str = "MYPART", model: str = "MYPART.step") -> bytes:
    """SnapEDA: everything flat at the archive root."""
    return _zip_bytes(
        {
            f"{part}.kicad_sym": _symbol_text(part),
            f"{part}.kicad_mod": _footprint_with_models(f"{part}_FP", f"/vendor/{model}"),
            model: STEP_BYTES,
        }
    )


def _samacsys_zip(part: str = "MYPART") -> bytes:
    """SamacSys / Component Search Engine: a `KiCad/` folder, 3D alongside."""
    return _zip_bytes(
        {
            f"KiCad/{part}.kicad_sym": _symbol_text(part),
            f"KiCad/{part}.kicad_mod": _footprint_with_models(
                f"{part}_FP", f"${{KIPRJMOD}}/{part}.stp"
            ),
            f"3D/{part}.stp": STEP_BYTES,
        }
    )


def _ultralibrarian_zip(part: str = "MYPART") -> bytes:
    """UltraLibrarian: a `KiCAD/` folder — capital CAD is the whole tell."""
    return _zip_bytes(
        {
            f"KiCAD/{part}.kicad_sym": _symbol_text(part),
            f"KiCAD/{part}.pretty/{part}_FP.kicad_mod": _footprint_with_models(
                f"{part}_FP", f"{part}.wrl"
            ),
            f"3D/{part}.wrl": WRL_BYTES,
        }
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


def _import_zip(client, part_id: str, raw: bytes, filename: str = "LIB_MYPART.zip", **form):
    data = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()}
    return client.post(
        f"/api/parts/{part_id}/eda/import",
        files={"file": (filename, raw, "application/zip")},
        data=data,
    )


def _import_library(client, raw: bytes, filename: str = "library.zip", **form):
    data = {k: str(v) for k, v in form.items() if v is not None}
    return client.post(
        "/api/eda/import",
        files={"file": (filename, raw, "application/octet-stream")},
        data=data,
    )


def _ok(response, expected: int = 200) -> dict:
    assert response.status_code == expected, response.text
    return response.json()["data"]


def _code(response) -> str:
    return response.json()["code"]


def _stored_text(client, sha: str, ext: str) -> str:
    r = client.get(f"/api/eda/files/{_ws_id(client)}/{sha}.{ext}")
    assert r.status_code == 200, r.text
    return r.text


def _sha_of(client, kind: str, row_id: str) -> str:
    rows = client.get(f"/api/eda/{kind}?limit=1000").json()["data"]
    return next(row["sha256"] for row in rows if row["id"] == row_id)


def _blob_count(client) -> int:
    directory = os.path.dirname(eda_storage.path_for(_ws_id(client), "x"))
    return len(os.listdir(directory)) if os.path.isdir(directory) else 0


def _audit_rows(db, action: str) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.action == action)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        ).scalars()
    )


# ---------------------------------------------------------------------
# Vendor detection
# ---------------------------------------------------------------------


def test_flat_archive_is_snapeda_and_wires_the_part(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")

    data = _ok(_import_zip(c, part_id, _snapeda_zip()))

    assert data["vendor"] == "snapeda"
    assert data["symbol"]["name"] == "MYPART"
    assert data["symbol"]["created"] is True
    assert data["footprint"]["name"] == "MYPART_FP"
    assert [row["kind"] for row in data["datafiles"]] == ["step"]
    assert data["part_eda_updated"] is True

    config = c.get(f"/api/parts/{part_id}/eda").json()["data"]
    assert config["symbol_id"] == data["symbol"]["id"]
    assert config["footprint_id"] == data["footprint"]["id"]


def test_kicad_folder_is_samacsys(authed_client):
    c = authed_client
    part_id = create_part(c, "Cap")
    data = _ok(_import_zip(c, part_id, _samacsys_zip()))
    assert data["vendor"] == "samacsys"


def test_capital_cad_folder_is_ultralibrarian(authed_client):
    c = authed_client
    part_id = create_part(c, "Inductor")
    data = _ok(_import_zip(c, part_id, _ultralibrarian_zip()))
    assert data["vendor"] == "ultralibrarian"


def test_the_vendor_is_recorded_as_the_rows_source(authed_client):
    """`source` is server-controlled provenance — phase 6 packages a
    library and needs to say where each entry came from."""
    c = authed_client
    part_id = create_part(c, "Cap")
    data = _ok(_import_zip(c, part_id, _samacsys_zip()))

    symbols = c.get("/api/eda/symbols").json()["data"]
    assert next(s for s in symbols if s["id"] == data["symbol"]["id"])["source"] == "samacsys"
    datafiles = c.get("/api/eda/datafiles").json()["data"]
    assert all(d["source"] == "samacsys" for d in datafiles)


def test_detect_vendor_is_case_sensitive():
    """`KiCad` and `KiCAD` differ by one letter's case and name two
    different vendors — folding case here would mislabel every import."""
    assert vendor_zip.detect_vendor(["KiCad/x.kicad_sym"]) == vendor_zip.VENDOR_SAMACSYS
    assert (
        vendor_zip.detect_vendor(["KiCAD/x.kicad_sym"]) == vendor_zip.VENDOR_ULTRALIBRARIAN
    )
    assert vendor_zip.detect_vendor(["x.kicad_sym"]) == vendor_zip.VENDOR_SNAPEDA


# ---------------------------------------------------------------------
# 3D model path rewriting
# ---------------------------------------------------------------------


def test_model_paths_are_rewritten_to_the_storage_variable(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    data = _ok(_import_zip(c, part_id, _snapeda_zip()))

    sha = _sha_of(c, "footprints", data["footprint"]["id"])
    body = _stored_text(c, sha, "kicad_mod")
    assert "${STOCKMGR_3D}/MYPART.step" in body
    assert "/vendor/MYPART.step" not in body
    # Placement survives the rewrite — only the path moved.
    assert "(offset" in body and "(rotate" in body


def test_model_entry_without_a_matching_file_is_dropped(authed_client):
    """A `(model …)` naming a file the archive didn't carry would leave
    KiCad reporting a missing model on every board placing it."""
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes(
        {
            "P.kicad_sym": _symbol_text("P"),
            "P.kicad_mod": _footprint_with_models("P_FP", "/vendor/P.step", "/vendor/ghost.wrl"),
            "P.step": STEP_BYTES,
        }
    )
    data = _ok(_import_zip(c, part_id, raw))

    body = _stored_text(c, _sha_of(c, "footprints", data["footprint"]["id"]), "kicad_mod")
    assert "${STOCKMGR_3D}/P.step" in body
    assert "ghost" not in body


def test_footprint_matching_no_models_keeps_its_paths_and_says_so(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes(
        {
            "P.kicad_sym": _symbol_text("P"),
            "P.kicad_mod": _footprint_with_models("P_FP", "/vendor/absent.step"),
        }
    )
    data = _ok(_import_zip(c, part_id, raw))

    body = _stored_text(c, _sha_of(c, "footprints", data["footprint"]["id"]), "kicad_mod")
    assert "/vendor/absent.step" in body
    assert any("3D model paths left unchanged" in s["reason"] for s in data["skipped"])


def test_step_and_wrl_of_one_model_both_attach_step_first(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes(
        {
            "P.kicad_sym": _symbol_text("P"),
            "P.kicad_mod": _footprint_with_models("P_FP", "/vendor/P.wrl"),
            "P.step": STEP_BYTES,
            "P.wrl": WRL_BYTES,
        }
    )
    data = _ok(_import_zip(c, part_id, raw))

    links = c.get(f"/api/eda/footprints/{data['footprint']['id']}/models").json()["data"]
    by_id = {row["id"]: row for row in c.get("/api/eda/datafiles").json()["data"]}
    assert [by_id[link["datafile_id"]]["kind"] for link in links] == ["step", "wrl"]
    # The path points at the STEP even though the footprint named the WRL.
    body = _stored_text(c, _sha_of(c, "footprints", data["footprint"]["id"]), "kicad_mod")
    assert "${STOCKMGR_3D}/P.step" in body


# ---------------------------------------------------------------------
# Part wiring
# ---------------------------------------------------------------------


def test_import_fills_only_empty_slots_by_default(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    first = _ok(_import_zip(c, part_id, _snapeda_zip("ALPHA", "ALPHA.step")))

    second = _ok(
        _import_zip(c, part_id, _snapeda_zip("BETA", "BETA.step"), filename="LIB_BETA.zip")
    )

    assert second["symbol"]["name"] == "BETA"
    config = c.get(f"/api/parts/{part_id}/eda").json()["data"]
    # The slots still hold what the FIRST import put there.
    assert config["symbol_id"] == first["symbol"]["id"]
    assert config["footprint_id"] == first["footprint"]["id"]


def test_overwrite_replaces_an_occupied_slot(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    _ok(_import_zip(c, part_id, _snapeda_zip("ALPHA", "ALPHA.step")))

    second = _ok(
        _import_zip(
            c,
            part_id,
            _snapeda_zip("BETA", "BETA.step"),
            filename="LIB_BETA.zip",
            overwrite=True,
        )
    )

    config = c.get(f"/api/parts/{part_id}/eda").json()["data"]
    assert config["symbol_id"] == second["symbol"]["id"]
    assert config["footprint_id"] == second["footprint"]["id"]


def test_import_replaces_an_external_reference_only_with_overwrite(authed_client):
    """An external `LibNick:Entry` is a filled slot, not an empty one."""
    c = authed_client
    part_id = create_part(c, "Resistor")
    c.put(f"/api/parts/{part_id}/eda", json={"symbol_ref_external": "Device:R"})

    _ok(_import_zip(c, part_id, _snapeda_zip()))
    config = c.get(f"/api/parts/{part_id}/eda").json()["data"]
    assert config["symbol_ref_external"] == "Device:R"
    assert config["symbol_id"] is None

    data = _ok(_import_zip(c, part_id, _snapeda_zip(), overwrite=True))
    config = c.get(f"/api/parts/{part_id}/eda").json()["data"]
    assert config["symbol_id"] == data["symbol"]["id"]
    # Both halves of a slot may never be set at once (CHECK constraint).
    assert config["symbol_ref_external"] is None


def test_import_never_touches_the_user_authored_fields(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    c.put(
        f"/api/parts/{part_id}/eda",
        json={"value": "10k", "keywords": "res smd", "exclude_from_bom": True},
    )

    _ok(_import_zip(c, part_id, _snapeda_zip()))

    config = c.get(f"/api/parts/{part_id}/eda").json()["data"]
    assert config["value"] == "10k"
    assert config["keywords"] == "res smd"
    assert config["exclude_from_bom"] is True


def test_an_archive_with_nothing_to_wire_leaves_no_empty_config(authed_client):
    """Importing only a 3D model wires nothing; the part shouldn't gain a
    blank configuration row as a side effect."""
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes({"P.step": STEP_BYTES})

    data = _ok(_import_zip(c, part_id, raw))

    assert data["part_eda_updated"] is False
    assert c.get(f"/api/parts/{part_id}/eda").json()["data"] is None


def test_spice_model_is_imported_and_wired(authed_client):
    c = authed_client
    part_id = create_part(c, "Diode")
    raw = _zip_bytes(
        {"P.kicad_sym": _symbol_text("P"), "P.sub": SPICE_BYTES}
    )
    data = _ok(_import_zip(c, part_id, raw))

    spice = next(row for row in data["datafiles"] if row["kind"] == "spice")
    assert c.get(f"/api/parts/{part_id}/eda").json()["data"]["spice_datafile_id"] == spice["id"]


def test_category_from_the_form_is_applied_to_created_entries(authed_client):
    c = authed_client
    category_id = c.post("/api/categories", json={"name": "Passives"}).json()["data"]["id"]
    part_id = create_part(c, "Resistor")

    data = _ok(_import_zip(c, part_id, _snapeda_zip(), category_id=category_id))

    symbols = c.get("/api/eda/symbols").json()["data"]
    assert next(s for s in symbols if s["id"] == data["symbol"]["id"])["category_id"] == category_id


def test_a_foreign_workspace_category_is_404(authed_client, other_client):
    foreign = other_client.post("/api/categories", json={"name": "Theirs"}).json()["data"]["id"]
    part_id = create_part(authed_client, "Resistor")

    r = _import_zip(authed_client, part_id, _snapeda_zip(), category_id=foreign)
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------
# Dedupe and name conflicts
# ---------------------------------------------------------------------


def test_reimporting_the_same_archive_reuses_rows_and_writes_no_new_blobs(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    first = _ok(_import_zip(c, part_id, _snapeda_zip()))
    blobs = _blob_count(c)

    second = _ok(_import_zip(c, part_id, _snapeda_zip()))

    assert second["symbol"]["id"] == first["symbol"]["id"]
    assert second["symbol"]["created"] is False
    assert second["footprint"]["created"] is False
    assert [row["created"] for row in second["datafiles"]] == [False]
    assert _blob_count(c) == blobs


def test_a_name_held_by_different_bytes_gets_a_numbered_suffix(authed_client):
    """One colliding member must not cost the user the whole archive —
    the single-file upload's 409 becomes a ` (2)` suffix here."""
    c = authed_client
    part_id = create_part(c, "Resistor")
    _ok(_import_zip(c, part_id, _snapeda_zip("MYPART", "MYPART.step")))

    # Same entry names, different content — a genuine conflict.
    other = _zip_bytes(
        {
            "MYPART.kicad_sym": _symbol_text("MYPART", value="47k"),
            "MYPART.kicad_mod": _footprint_text("MYPART_FP", descr="different"),
        }
    )
    data = _ok(_import_zip(c, create_part(c, "Second"), other))

    assert data["symbol"]["name"] == "MYPART (2)"
    assert data["footprint"]["name"] == "MYPART_FP (2)"


# ---------------------------------------------------------------------
# Archive guards
# ---------------------------------------------------------------------


def test_a_legacy_only_archive_is_422_with_conversion_advice(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes({"MYPART.lib": LEGACY_LIB_BYTES, "MYPART.dcm": b"EESchema-DOCLIB\n"})

    r = _import_zip(c, part_id, raw)

    assert r.status_code == 422, r.text
    assert _code(r) == "eda.legacy_format"
    assert "kicad-cli" in r.json()["status"]["message"]


def test_a_legacy_lib_alongside_a_modern_symbol_is_only_skipped(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes({"P.kicad_sym": _symbol_text("P"), "P.lib": LEGACY_LIB_BYTES})

    data = _ok(_import_zip(c, part_id, raw))

    assert data["symbol"]["name"] == "P"
    assert any("legacy KiCad 5" in s["reason"] for s in data["skipped"])


def test_a_lib_that_is_neither_spice_nor_kicad_is_skipped_not_fatal(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes({"P.kicad_sym": _symbol_text("P"), "P.lib": b"random text\n"})

    data = _ok(_import_zip(c, part_id, raw))

    assert data["symbol"]["name"] == "P"
    assert any("neither" in s["reason"] for s in data["skipped"])


def test_too_many_members_is_422_before_anything_is_extracted(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes({f"f{i}.step": STEP_BYTES for i in range(vendor_zip.MAX_MEMBERS + 1)})

    r = _import_zip(c, part_id, raw)

    assert r.status_code == 422, r.text
    assert _code(r) == "eda.archive_too_large"
    assert _blob_count(c) == 0


def test_a_declared_uncompressed_size_over_the_cap_is_422(authed_client):
    """The zip-bomb case: a few KiB on the wire, tens of MiB inflated."""
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes({"big.step": b"\0" * (vendor_zip.MAX_UNCOMPRESSED_BYTES + 1)})
    assert len(raw) < 1024 * 1024

    r = _import_zip(c, part_id, raw)

    assert r.status_code == 422, r.text
    assert _code(r) == "eda.archive_too_large"


def test_a_file_that_is_not_a_zip_is_422(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")

    r = _import_zip(c, part_id, b"not a zip at all")

    assert r.status_code == 422, r.text
    assert _code(r) == "eda.invalid_archive"


def test_an_archive_with_nothing_importable_is_422(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes({"readme.txt": b"hello", "logo.png": b"\x89PNG\r\n\x1a\n"})

    r = _import_zip(c, part_id, raw)

    assert r.status_code == 422, r.text
    assert _code(r) == "eda.no_entries"


def test_an_empty_upload_is_422(authed_client):
    part_id = create_part(authed_client, "Resistor")
    r = _import_zip(authed_client, part_id, b"")
    assert r.status_code == 422, r.text
    assert _code(r) == "eda.empty_file"


# ---------------------------------------------------------------------
# Ambiguity
# ---------------------------------------------------------------------


def test_several_symbols_with_no_way_to_choose_is_422(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes({"lib.kicad_sym": _symbol_lib("ALPHA", "BETA", "GAMMA")})

    r = _import_zip(c, part_id, raw, filename="bundle.zip")

    assert r.status_code == 422, r.text
    assert _code(r) == "eda.multiple_symbols"
    body = r.json()
    assert body["symbol_count"] == 3
    assert all(len(name) <= 80 for name in body["symbol_names"])


def test_several_symbols_are_resolved_by_the_archive_name(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes({"lib.kicad_sym": _symbol_lib("ALPHA", "BETA")})

    data = _ok(_import_zip(c, part_id, raw, filename="LIB_BETA.zip"))

    assert data["symbol"]["name"] == "BETA"


def test_several_symbols_are_resolved_by_the_parts_mpn(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor", mpn="ALPHA")
    raw = _zip_bytes({"lib.kicad_sym": _symbol_lib("ALPHA", "BETA")})

    data = _ok(_import_zip(c, part_id, raw, filename="bundle.zip"))

    assert data["symbol"]["name"] == "ALPHA"


def test_several_footprints_are_resolved_by_the_symbols_footprint_property(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes(
        {
            "P.kicad_sym": _symbol_with_footprint("P", "MyLib:CHOSEN"),
            "a.kicad_mod": _footprint_text("CHOSEN"),
            "b.kicad_mod": _footprint_text("OTHER"),
        }
    )

    data = _ok(_import_zip(c, part_id, raw, filename="bundle.zip"))

    assert data["footprint"]["name"] == "CHOSEN"


def test_several_unreferenced_footprints_are_422(authed_client):
    c = authed_client
    part_id = create_part(c, "Resistor")
    raw = _zip_bytes(
        {
            "P.kicad_sym": _symbol_text("P"),
            "a.kicad_mod": _footprint_text("FIRST"),
            "b.kicad_mod": _footprint_text("SECOND"),
        }
    )

    r = _import_zip(c, part_id, raw, filename="bundle.zip")

    assert r.status_code == 422, r.text
    assert _code(r) == "eda.multiple_footprints"
    assert r.json()["footprint_count"] == 2


# ---------------------------------------------------------------------
# Library-level import
# ---------------------------------------------------------------------


def test_library_import_takes_every_symbol_and_wires_nothing(authed_client):
    c = authed_client
    raw = _zip_bytes(
        {
            "lib.kicad_sym": _symbol_lib("ALPHA", "BETA", "GAMMA"),
            "fp.kicad_mod": _footprint_text("FP"),
        }
    )

    data = _ok(_import_library(c, raw))

    assert [row["name"] for row in data["symbols"]] == ["ALPHA", "BETA", "GAMMA"]
    assert data["created"] == 4
    assert data["reused"] == 0
    assert len(c.get("/api/eda/symbols").json()["data"]) == 3


def test_library_import_accepts_a_bare_multi_symbol_file(authed_client):
    """The single-upload route's 422 tells the user to come here; this is
    the endpoint honouring that promise."""
    c = authed_client
    refused = c.post(
        "/api/eda/symbols",
        files={"file": ("lib.kicad_sym", _symbol_lib("A", "B").encode(), "text/plain")},
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "eda.multiple_symbols"

    data = _ok(
        _import_library(c, _symbol_lib("A", "B").encode(), filename="lib.kicad_sym")
    )
    assert [row["name"] for row in data["symbols"]] == ["A", "B"]


def test_library_reimport_reports_reused_rows(authed_client):
    c = authed_client
    raw = _zip_bytes({"lib.kicad_sym": _symbol_lib("ALPHA", "BETA")})
    _ok(_import_library(c, raw))

    data = _ok(_import_library(c, raw))

    assert data["created"] == 0
    assert data["reused"] == 2


def test_library_import_caps_the_entries_it_creates(authed_client):
    c = authed_client
    names = [f"SYM{i}" for i in range(vendor_zip.MAX_ENTRIES + 5)]
    raw = _zip_bytes({"lib.kicad_sym": _symbol_lib(*names)})

    data = _ok(_import_library(c, raw))

    assert len(data["symbols"]) == vendor_zip.MAX_ENTRIES
    assert any("more than" in s["reason"] for s in data["skipped"])


# ---------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------


def test_a_part_import_audits_each_entry_and_the_wiring(authed_client, db):
    c = authed_client
    part_id = create_part(c, "Resistor")
    data = _ok(_import_zip(c, part_id, _snapeda_zip()))

    assert len(_audit_rows(db, "eda_symbol.uploaded")) == 1
    assert len(_audit_rows(db, "eda_footprint.uploaded")) == 1
    assert len(_audit_rows(db, "eda_datafile.uploaded")) == 1

    rows = _audit_rows(db, "part_eda.imported")
    assert len(rows) == 1
    assert rows[0].target_type == "part_eda"
    assert rows[0].target_ids == [uuid.UUID(part_id)]
    assert rows[0].comment == "vendor=snapeda,files=3"
    assert rows[0].workspace_id is not None and rows[0].user_id is not None
    assert data["symbol"]["created"] is True


def test_a_reimport_audits_the_wiring_but_not_the_reused_entries(authed_client, db):
    c = authed_client
    part_id = create_part(c, "Resistor")
    _ok(_import_zip(c, part_id, _snapeda_zip()))

    _ok(_import_zip(c, part_id, _snapeda_zip()))

    assert len(_audit_rows(db, "eda_symbol.uploaded")) == 1
    assert len(_audit_rows(db, "part_eda.imported")) == 2


def test_a_large_library_import_collapses_to_one_audit_row(authed_client, db):
    """21 near-identical rows are not a trail anyone reads — past 20 the
    importer writes counts instead."""
    c = authed_client
    names = [f"SYM{i}" for i in range(25)]
    raw = _zip_bytes({"lib.kicad_sym": _symbol_lib(*names)})

    _ok(_import_library(c, raw))

    assert _audit_rows(db, "eda_symbol.uploaded") == []
    rows = _audit_rows(db, "eda_library.imported")
    assert len(rows) == 1
    assert rows[0].comment == "vendor=snapeda,symbols=25,footprints=0,datafiles=0"


def test_a_small_library_import_audits_each_entry(authed_client, db):
    c = authed_client
    raw = _zip_bytes({"lib.kicad_sym": _symbol_lib("ALPHA", "BETA")})

    _ok(_import_library(c, raw))

    assert len(_audit_rows(db, "eda_symbol.uploaded")) == 2
    assert _audit_rows(db, "eda_library.imported") == []


def test_a_rejected_import_writes_no_audit_row(authed_client, db):
    c = authed_client
    part_id = create_part(c, "Resistor")
    before = len(_audit_rows(db, "part_eda.imported"))

    assert _import_zip(c, part_id, b"not a zip").status_code == 422

    assert len(_audit_rows(db, "part_eda.imported")) == before


# ---------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------


def test_importing_into_a_foreign_part_is_404(authed_client, other_client):
    part_a = create_part(authed_client, "A's part")

    r = _import_zip(other_client, part_a, _snapeda_zip())

    assert r.status_code == 404, r.text
    assert other_client.get("/api/eda/symbols").json()["data"] == []


def test_fetching_lcsc_into_a_foreign_part_is_404(authed_client, other_client):
    part_a = create_part(authed_client, "A's part")

    r = other_client.post(
        f"/api/parts/{part_a}/eda/fetch-lcsc", json={"lcsc_id": "C25804"}
    )

    assert r.status_code == 404, r.text


def test_an_imported_library_is_not_visible_to_another_workspace(authed_client, other_client):
    _ok(_import_library(authed_client, _zip_bytes({"lib.kicad_sym": _symbol_lib("A")})))

    assert other_client.get("/api/eda/symbols").json()["data"] == []


# ---------------------------------------------------------------------
# LCSC / EasyEDA
#
# The `easyeda2kicad` classes `lcsc.fetch_plan` drives are replaced, not
# `fetch_plan` itself — the glue that writes a temp directory and reads
# the converter's output back is exactly where a bug would hide.
# ---------------------------------------------------------------------

LCSC_SYMBOL = _symbol_text("C25804_R", value="10k")
LCSC_FOOTPRINT_NAME = "R0402"


class _FakeApi:
    """Stands in for `EasyedaApi`. `cad_data` decides what happens."""

    cad_data: dict | Exception = {"packageDetail": {}}

    def __init__(self, *_a, **_kw):
        pass

    def get_cad_data_of_component(self, lcsc_id: str):
        if isinstance(self.cad_data, Exception):
            raise self.cad_data
        return self.cad_data


class _FakeSymbolImporter:
    def __init__(self, easyeda_cp_cad_data):
        pass

    def get_symbol(self):
        return object()


class _FakeSymbolExporter:
    def __init__(self, symbol, lib_path=None, **_kw):
        pass

    def export(self, footprint_lib_name: str) -> str:
        return LCSC_SYMBOL


class _FakeFootprint:
    class info:
        name = LCSC_FOOTPRINT_NAME


class _FakeFootprintImporter:
    def __init__(self, easyeda_cp_cad_data):
        pass

    def get_footprint(self):
        return _FakeFootprint()


class _FakeFootprintExporter:
    def __init__(self, footprint):
        pass

    def export(self, footprint_full_path: str, model_3d_path: str, model_3d_extension="wrl"):
        text = _footprint_with_models(
            LCSC_FOOTPRINT_NAME, f"{model_3d_path}/{LCSC_FOOTPRINT_NAME}.{model_3d_extension}"
        )
        with open(footprint_full_path, "w", encoding="utf-8") as fh:
            fh.write(text)


class _FakeModelImporter:
    def __init__(self, easyeda_cp_cad_data, download_raw_3d_model, api=None, **_kw):
        pass

    output = object()


class _FakeModelExporter:
    def __init__(self, model_3d):
        pass

    def export(self, output_dir: str, overwrite: bool = True) -> bool:
        os.makedirs(output_dir, exist_ok=True)
        for suffix, content in ((".step", STEP_BYTES), (".wrl", WRL_BYTES)):
            with open(os.path.join(output_dir, LCSC_FOOTPRINT_NAME + suffix), "wb") as fh:
                fh.write(content)
        return True


@pytest.fixture
def fake_easyeda(monkeypatch):
    """Swap in the fakes. `lcsc.fetch_plan` imports these lazily, so
    patching the module attributes is enough."""
    monkeypatch.setattr(_FakeApi, "cad_data", {"packageDetail": {}})
    monkeypatch.setattr(
        "easyeda2kicad.easyeda.easyeda_api.EasyedaApi", _FakeApi, raising=True
    )
    monkeypatch.setattr(
        "easyeda2kicad.easyeda.easyeda_importer.EasyedaSymbolImporter",
        _FakeSymbolImporter,
    )
    monkeypatch.setattr(
        "easyeda2kicad.easyeda.easyeda_importer.EasyedaFootprintImporter",
        _FakeFootprintImporter,
    )
    monkeypatch.setattr(
        "easyeda2kicad.easyeda.easyeda_importer.Easyeda3dModelImporter", _FakeModelImporter
    )
    monkeypatch.setattr(
        "easyeda2kicad.kicad.export_kicad_symbol.ExporterSymbolKicad", _FakeSymbolExporter
    )
    monkeypatch.setattr(
        "easyeda2kicad.kicad.export_kicad_footprint.ExporterFootprintKicad",
        _FakeFootprintExporter,
    )
    monkeypatch.setattr(
        "easyeda2kicad.kicad.export_kicad_3d_model.Exporter3dModelKicad", _FakeModelExporter
    )
    return _FakeApi


def _fetch(client, part_id: str, **body):
    payload = {"lcsc_id": "C25804"}
    payload.update(body)
    return client.post(f"/api/parts/{part_id}/eda/fetch-lcsc", json=payload)


def test_lcsc_fetch_wires_the_converted_artifacts(authed_client, fake_easyeda):
    c = authed_client
    part_id = create_part(c, "Resistor")

    data = _ok(_fetch(c, part_id))

    assert data["vendor"] == "easyeda"
    assert data["symbol"]["name"] == "C25804_R"
    assert data["footprint"]["name"] == LCSC_FOOTPRINT_NAME
    assert sorted(row["kind"] for row in data["datafiles"]) == ["step", "wrl"]
    assert data["part_eda_updated"] is True

    config = c.get(f"/api/parts/{part_id}/eda").json()["data"]
    assert config["symbol_id"] == data["symbol"]["id"]

    symbols = c.get("/api/eda/symbols").json()["data"]
    assert symbols[0]["source"] == "easyeda"


def test_lcsc_footprint_model_path_is_rewritten_like_a_zip_import(authed_client, fake_easyeda):
    c = authed_client
    part_id = create_part(c, "Resistor")
    data = _ok(_fetch(c, part_id))

    body = _stored_text(c, _sha_of(c, "footprints", data["footprint"]["id"]), "kicad_mod")
    assert f"${{STOCKMGR_3D}}/{LCSC_FOOTPRINT_NAME}.step" in body


def test_lcsc_fetch_audits_the_import(authed_client, fake_easyeda, db):
    c = authed_client
    part_id = create_part(c, "Resistor")
    _ok(_fetch(c, part_id))

    rows = _audit_rows(db, "part_eda.imported")
    assert len(rows) == 1
    assert rows[0].comment == "vendor=easyeda,files=4"


def test_an_unknown_lcsc_id_is_404(authed_client, fake_easyeda, monkeypatch):
    monkeypatch.setattr(_FakeApi, "cad_data", {})
    part_id = create_part(authed_client, "Resistor")

    r = _fetch(authed_client, part_id)

    assert r.status_code == 404, r.text
    assert _code(r) == "eda.lcsc_not_found"


def test_an_unreachable_easyeda_is_502(authed_client, fake_easyeda, monkeypatch):
    monkeypatch.setattr(_FakeApi, "cad_data", OSError("connection refused"))
    part_id = create_part(authed_client, "Resistor")

    r = _fetch(authed_client, part_id)

    assert r.status_code == 502, r.text
    assert _code(r) == "eda.lcsc_unavailable"


@pytest.mark.parametrize("bad", ["25804", "C", "C-1", "../C1", "C" + "9" * 11, ""])
def test_a_malformed_lcsc_id_is_422(authed_client, bad):
    part_id = create_part(authed_client, "Resistor")
    r = authed_client.post(f"/api/parts/{part_id}/eda/fetch-lcsc", json={"lcsc_id": bad})
    assert r.status_code == 422, r.text


def test_the_lcsc_body_rejects_unknown_fields(authed_client):
    part_id = create_part(authed_client, "Resistor")
    r = authed_client.post(
        f"/api/parts/{part_id}/eda/fetch-lcsc",
        json={"lcsc_id": "C25804", "nope": 1},
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------


def test_the_import_routes_carry_workspace_scoped_rate_limits():
    """The limiter is disabled outside prod, so the decorators can only
    be pinned by reading them — a refactor that drops one would
    otherwise pass every functional test."""
    from app.api.routes import eda_import

    source = inspect.getsource(eda_import)
    assert source.count('@limiter.limit(_IMPORT_RATE, key_func=workspace_key)') == 2
    assert '@limiter.limit(_LCSC_RATE, key_func=workspace_key)' in source
    assert eda_import._IMPORT_RATE == "10/minute"
    assert eda_import._LCSC_RATE == "5/minute"


@pytest.mark.real_db
def test_concurrent_imports_into_one_part_do_not_500_on_the_unique_index():
    """Two imports for a part with no config yet both find nothing and
    race to INSERT; `uq_part_eda_part` lets one through. The loser has to
    recover onto the winning row, not surface a 500 — the same recovery
    `service.upsert_part_eda` makes for the PUT path.

    real_db: the two requests run on separate connections, so the first
    insert has to be genuinely committed for the second to collide with
    it — under the savepoint fixture they'd share one transaction and
    never race at all.
    """
    from concurrent.futures import ThreadPoolExecutor

    a = TestClient(app)
    signup_user(a)
    part_id = create_part(a, "Contended")

    def do_import(part: str):
        client = TestClient(app)
        client.cookies = a.cookies
        return _import_zip(
            client, part_id, _snapeda_zip(part, f"{part}.step"), filename=f"LIB_{part}.zip"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            f.result() for f in [pool.submit(do_import, "ALPHA"), pool.submit(do_import, "BETA")]
        ]

    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    config = a.get(f"/api/parts/{part_id}/eda").json()["data"]
    assert config["symbol_id"] is not None


# ---------------------------------------------------------------------
# Review fixes — each test pins one finding
# ---------------------------------------------------------------------


def test_a_suffixed_row_holds_bytes_carrying_the_suffixed_name(authed_client):
    """CODE HIGH-1. A symbol's entry name lives INSIDE the file, so the
    ` (2)` conflict suffix has to rename the s-expression too. A row
    called `MYPART (2)` pointing at bytes that say `(symbol "MYPART")`
    breaks the invariant `service._rewrite_stored_entry_name` exists to
    hold — phase 5 resolves `LibNick:Entry` against the file content.
    """
    c = authed_client
    _ok(_import_zip(c, create_part(c, "First"), _snapeda_zip("MYPART", "MYPART.step")))

    clashing = _zip_bytes(
        {
            "MYPART.kicad_sym": _symbol_text("MYPART", value="47k"),
            "MYPART.kicad_mod": _footprint_text("MYPART_FP", descr="different"),
        }
    )
    data = _ok(_import_zip(c, create_part(c, "Second"), clashing))

    assert data["symbol"]["name"] == "MYPART (2)"
    body = _stored_text(c, _sha_of(c, "symbols", data["symbol"]["id"]), "kicad_sym")
    assert body.lstrip().startswith('(symbol "MYPART (2)"')

    assert data["footprint"]["name"] == "MYPART_FP (2)"
    fp = _stored_text(c, _sha_of(c, "footprints", data["footprint"]["id"]), "kicad_mod")
    assert fp.lstrip().startswith('(footprint "MYPART_FP (2)"')


def test_a_nul_byte_in_an_archived_symbol_is_422_not_500(authed_client):
    """SEC HIGH-2. A lone NUL is valid UTF-8, so a bare `.decode()` lets
    it through to Postgres, where it lands as a DataError 500."""
    c = authed_client
    raw = _zip_bytes({"P.kicad_sym": _symbol_text("BAD\x00NAME").encode()})

    r = _import_zip(c, create_part(c, "Resistor"), raw)

    assert r.status_code == 422, r.text
    assert _code(r) == "eda.invalid_file"


def test_a_nul_byte_in_a_bare_symbol_library_is_422_not_500(authed_client):
    r = _import_library(
        authed_client, _symbol_lib("A\x00B").encode(), filename="lib.kicad_sym"
    )
    assert r.status_code == 422, r.text
    assert _code(r) == "eda.invalid_file"


def test_an_explicit_footprint_reference_beats_a_filename_hint(authed_client):
    """CODE HIGH-3. The symbol names `R0402_HandSolder` outright, but the
    archive is `LIB_R0402.zip` and a footprint called `R0402` also
    exists. Unioning the two sources of evidence let the weak one veto
    the strong one and turned an unambiguous archive into a 422."""
    c = authed_client
    raw = _zip_bytes(
        {
            "P.kicad_sym": _symbol_with_footprint("P", "MyLib:R0402_HandSolder"),
            "a.kicad_mod": _footprint_text("R0402_HandSolder"),
            "b.kicad_mod": _footprint_text("R0402"),
        }
    )

    data = _ok(_import_zip(c, create_part(c, "Resistor"), raw, filename="LIB_R0402.zip"))

    assert data["footprint"]["name"] == "R0402_HandSolder"


def test_a_symbol_linked_to_an_archive_footprint_beats_a_filename_hint(authed_client):
    """The same precedence on the symbol side: the entry that names one
    of this archive's footprints wins over a filename that happens to
    match a different symbol."""
    c = authed_client
    raw = _zip_bytes(
        {
            "lib.kicad_sym": _wrap_lib(
                _symbol_with_footprint("LINKED", "MyLib:THE_FP"), _symbol_text("R0402")
            ),
            "fp.kicad_mod": _footprint_text("THE_FP"),
        }
    )

    data = _ok(_import_zip(c, create_part(c, "Resistor"), raw, filename="LIB_R0402.zip"))

    assert data["symbol"]["name"] == "LINKED"


def test_the_parse_budget_stops_retaining_before_memory_runs_away(authed_client):
    """SEC HIGH-1. A parsed node tree runs ~20x its source text, and the
    old code parsed and retained EVERY entry before trimming — 133 KiB of
    zip reached 1.2 GiB RSS. The budget has to bite while walking."""
    c = authed_client
    # Each member is ~800 KiB of legal symbol text and compresses to
    # almost nothing, so this is a small upload that would inflate past
    # the 8 MiB parse budget.
    padding = "x" * 800_000
    members = {f"s{i}.kicad_sym": _symbol_text(f"SYM{i}", value=padding) for i in range(14)}
    raw = _zip_bytes(members)
    assert len(raw) < 200_000

    data = _ok(_import_library(c, raw))

    assert 0 < len(data["symbols"]) < 14
    assert any("parseable text" in s["reason"] for s in data["skipped"])


def test_a_member_lying_about_its_size_costs_one_chunk_not_a_full_cap():
    """SEC MED. `_read_member` used to ask zlib for `cap + 1` bytes
    regardless of the declared size, so 200 members each understating
    themselves bought ~2 GiB of inflation from a 2 MiB upload. The
    chunked read stops one chunk past the cap and charges what it
    actually inflated."""
    import zipfile as zf_mod

    from app.domain.eda import vendor_zip as vz

    raw = _zip_bytes({"big.step": b"A" * 300_000})
    zf = zf_mod.ZipFile(io.BytesIO(raw))
    info = zf.infolist()[0]
    # The central directory now understates the member by 30,000x.
    info.file_size = 10

    budget = vz._Budget()
    assert vz._read_member(zf, info, cap=1024, budget=budget) is None
    assert budget.inflated <= 1024 + vz._CHUNK_BYTES


def test_the_running_inflate_total_rejects_what_the_headers_understated():
    from app.domain.eda import vendor_zip as vz

    budget = vz._Budget()
    budget.inflate(vz.MAX_UNCOMPRESSED_BYTES)
    with pytest.raises(Exception) as excinfo:
        budget.inflate(1)
    assert excinfo.value.detail["code"] == "eda.archive_too_large"


def test_a_long_member_name_is_clamped_before_it_reaches_the_response(authed_client):
    """SEC LOW. 200 members × an unclamped name is megabytes of
    caller-controlled text in the response body."""
    c = authed_client
    raw = _zip_bytes(
        {"P.kicad_sym": _symbol_text("P"), ("n" * 300) + ".txt": b"junk"}
    )

    data = _ok(_import_zip(c, create_part(c, "Resistor"), raw))

    assert data["skipped"]
    assert all(len(s["filename"]) <= 80 for s in data["skipped"])


def test_a_datafile_only_archive_still_rejects_a_foreign_category(
    authed_client, other_client
):
    """ISOLATION. `category_id` was validated only on the symbol and
    footprint paths, so a STEP-only archive skipped the check entirely
    and answered 200 for another workspace's category."""
    foreign = other_client.post("/api/categories", json={"name": "Theirs"}).json()["data"]["id"]
    c = authed_client
    raw = _zip_bytes({"P.step": STEP_BYTES})

    assert _import_zip(c, create_part(c, "R"), raw, category_id=foreign).status_code == 404
    assert _import_library(c, raw, category_id=foreign).status_code == 404
    assert c.get("/api/eda/datafiles").json()["data"] == []


# ---------------------------------------------------------------------
# LCSC review fixes
# ---------------------------------------------------------------------


class _NamedModel:
    """Stands in for an `Ee3dModel` — all `lcsc` touches is `.name`."""

    def __init__(self, name: str):
        self.name = name


class _NamingModelImporter:
    """Yields a model whose name comes straight from upstream JSON."""

    upstream_name = "../../PWNED"

    def __init__(self, **_kw):
        self.output = _NamedModel(self.upstream_name)


class _FaithfulModelExporter:
    """Writes `output_dir/{name}.{ext}`, exactly as the real exporter does."""

    def __init__(self, model_3d):
        self.output = model_3d

    def export(self, output_dir: str, overwrite: bool = True) -> bool:
        base = os.path.realpath(output_dir)
        os.makedirs(base, exist_ok=True)
        for suffix, content in ((".step", STEP_BYTES), (".wrl", WRL_BYTES)):
            target = os.path.join(base, f"{self.output.name}{suffix}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(content)
        return True


def _use_naming_exporter(monkeypatch):
    monkeypatch.setattr(
        "easyeda2kicad.easyeda.easyeda_importer.Easyeda3dModelImporter",
        _NamingModelImporter,
    )
    monkeypatch.setattr(
        "easyeda2kicad.kicad.export_kicad_3d_model.Exporter3dModelKicad",
        _FaithfulModelExporter,
    )


def test_an_upstream_model_name_cannot_escape_the_conversion_directory(
    authed_client, fake_easyeda, monkeypatch, tmp_path
):
    """SEC HIGH. `easyeda2kicad` builds its output path from EasyEDA's
    own JSON `title`, so `../../PWNED` was a remote arbitrary file write
    outside the TemporaryDirectory."""
    _use_naming_exporter(monkeypatch)
    monkeypatch.setattr(_NamingModelImporter, "upstream_name", f"{tmp_path}/PWNED")
    part_id = create_part(authed_client, "Resistor")

    data = _ok(_fetch(authed_client, part_id))

    # The name was flattened before anything was written, so nothing
    # landed at the absolute path upstream asked for.
    assert not (tmp_path / "PWNED.step").exists()
    assert not (tmp_path / "PWNED.wrl").exists()
    for row in data["datafiles"]:
        assert "/" not in row["name"] and ".." not in row["name"]


def test_a_converted_model_pointing_outside_the_directory_is_skipped(
    authed_client, fake_easyeda, monkeypatch, tmp_path
):
    """Belt-and-braces after the name sanitiser: a symlink planted inside
    the conversion directory must not become an importable file."""
    outside = tmp_path / "secret.step"
    outside.write_bytes(b"ISO-10303-21;\nSECRET;\n")

    class _SymlinkExporter:
        def __init__(self, model_3d):
            self.output = model_3d

        def export(self, output_dir: str, overwrite: bool = True) -> bool:
            os.makedirs(output_dir, exist_ok=True)
            os.symlink(outside, os.path.join(output_dir, "escaped.step"))
            return True

    monkeypatch.setattr(
        "easyeda2kicad.kicad.export_kicad_3d_model.Exporter3dModelKicad", _SymlinkExporter
    )
    part_id = create_part(authed_client, "Resistor")

    data = _ok(_fetch(authed_client, part_id))

    assert data["datafiles"] == []
    assert any("outside the conversion directory" in s["reason"] for s in data["skipped"])


def test_an_oversize_converted_model_is_skipped_not_imported(
    authed_client, fake_easyeda, monkeypatch
):
    """SEC MED. The read-back was unbounded — `validated_datafile` only
    checks the leading magic, so an upstream model of any size was
    loaded whole."""
    # Below both fixture payloads (62 and 54 bytes).
    monkeypatch.setitem(eda_storage.MAX_BYTES_BY_KIND, "step", 16)
    monkeypatch.setitem(eda_storage.MAX_BYTES_BY_KIND, "wrl", 16)
    part_id = create_part(authed_client, "Resistor")

    data = _ok(_fetch(authed_client, part_id))

    assert data["datafiles"] == []
    assert any("exceeds the size limit" in s["reason"] for s in data["skipped"])
    # The symbol and footprint still imported — one oversize member costs
    # only itself.
    assert data["symbol"] is not None


def test_the_fetch_budget_is_checked_before_the_download_stage(
    authed_client, fake_easyeda, monkeypatch
):
    """SEC MED. The deadline used to be consulted at exactly one point,
    so a slow upstream could run three 30s urllib calls back to back."""
    from app.domain.eda import lcsc as lcsc_mod

    calls = {"n": 0}

    def budget(_deadline: float) -> bool:
        # True for the post-fetch and footprint checks, then exhausted.
        calls["n"] += 1
        return calls["n"] <= 2

    monkeypatch.setattr(lcsc_mod, "_in_budget", budget)
    part_id = create_part(authed_client, "Resistor")

    data = _ok(_fetch(authed_client, part_id))

    assert data["symbol"] is not None
    assert data["footprint"] is not None
    assert data["datafiles"] == []
    assert any("ran out of time" in s["reason"] for s in data["skipped"])
    assert calls["n"] >= 3


def test_a_budget_exhausted_before_conversion_is_502(
    authed_client, fake_easyeda, monkeypatch
):
    from app.domain.eda import lcsc as lcsc_mod

    monkeypatch.setattr(lcsc_mod, "_in_budget", lambda _deadline: False)
    part_id = create_part(authed_client, "Resistor")

    r = _fetch(authed_client, part_id)

    assert r.status_code == 502, r.text
    assert _code(r) == "eda.lcsc_unavailable"


# ---------------------------------------------------------------------
# Addendum fixes
# ---------------------------------------------------------------------


def test_a_datafile_suffix_lands_before_the_extension(authed_client):
    """CODE MED. `P.step (2)` is not a STEP file as far as KiCad is
    concerned — it picks the 3D plugin by extension — and the row name is
    what the rewritten `(model …)` path points at, so the wrong form
    fails silently on the board."""
    c = authed_client
    _ok(_import_zip(c, create_part(c, "First"), _snapeda_zip("P", "P.step")))

    # Same file names, different bytes: a real conflict on every entry.
    other = _zip_bytes(
        {
            "P.kicad_sym": _symbol_text("P", value="47k"),
            "P.kicad_mod": _footprint_with_models("P_FP2", "/vendor/P.step"),
            "P.step": STEP_BYTES + b"DIFFERENT\n",
        }
    )
    data = _ok(_import_zip(c, create_part(c, "Second"), other))

    assert [row["name"] for row in data["datafiles"]] == ["P (2).step"]
    body = _stored_text(c, _sha_of(c, "footprints", data["footprint"]["id"]), "kicad_mod")
    assert "${STOCKMGR_3D}/P (2).step" in body


def test_a_partially_dropped_model_reference_is_reported(authed_client):
    """CODE MED. When SOME paths resolve the footprint imports and looks
    fine, so a dropped model is invisible unless it's named."""
    c = authed_client
    raw = _zip_bytes(
        {
            "P.kicad_sym": _symbol_text("P"),
            "P.kicad_mod": _footprint_with_models(
                "P_FP", "/vendor/P.step", "/vendor/ghost.wrl"
            ),
            "P.step": STEP_BYTES,
        }
    )

    data = _ok(_import_zip(c, create_part(c, "Resistor"), raw))

    dropped = [s for s in data["skipped"] if "dropped" in s["reason"]]
    assert [s["filename"] for s in dropped] == ["/vendor/ghost.wrl"]


def test_entries_narrowed_away_from_a_part_are_reported(authed_client):
    """CODE MED. Narrowing a library archive down to one part silently
    discarded the rest — the module's own rule is that anything not taken
    is a note."""
    c = authed_client
    raw = _zip_bytes(
        {
            "lib.kicad_sym": _symbol_lib("ALPHA", "BETA", "GAMMA"),
            "a.kicad_mod": _footprint_text("ALPHA"),
        }
    )

    data = _ok(_import_zip(c, create_part(c, "Resistor"), raw, filename="LIB_ALPHA.zip"))

    assert data["symbol"]["name"] == "ALPHA"
    notes = [s["reason"] for s in data["skipped"] if "not wired to this part" in s["reason"]]
    assert len(notes) == 2


def test_the_outer_lcsc_wait_leaves_room_for_the_inner_budget():
    """CODE MED. With both timeouts equal the outer `asyncio.wait_for`
    fired first every time, making the per-stage deadline checks — and
    the branch that skips the 3D download — unreachable."""
    from app.api.routes import eda_import as route_mod
    from app.domain.eda import lcsc as lcsc_mod

    assert lcsc_mod.HARD_TIMEOUT_SECONDS > lcsc_mod.FETCH_BUDGET_SECONDS
    assert "lcsc.HARD_TIMEOUT_SECONDS" in inspect.getsource(route_mod._fetch_lcsc_plan)
