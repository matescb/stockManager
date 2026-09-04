"""`/api/eda/{symbols,footprints}/{id}/preview.kicad_*` — the 2D preview documents.

These two routes exist because KiCanvas, the viewer the CAD tab embeds,
cannot read the `.kicad_sym` / `.kicad_mod` files this domain stores;
`domain/eda/preview.py` explains the wrapping. What the viewer needs from
the documents is narrow and easy to break invisibly — a wrong `lib_id`
renders a blank symbol rather than an error — so the assertions here are
about the *shape KiCanvas parses*, not just about a 200.

Isolation follows the house pattern (`test_eda.py`): a second signup gets
a second workspace and every cross-workspace id must come back 404.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.domain.eda import sexpr
from app.main import app
from tests._factories import signup_user

# ---------------------------------------------------------------------
# Fixture content + helpers
# ---------------------------------------------------------------------


def _symbol_text(name: str = "R") -> str:
    return (
        f'(symbol "{name}" (in_bom yes) (on_board yes)\n'
        f'  (property "Reference" "R" (at 0 0 0))\n'
        f'  (property "Value" "{name}" (at 0 0 0))\n'
        f'  (symbol "{name}_0_1"\n'
        f"    (rectangle (start -1 -2.54) (end 1 2.54))\n"
        f"  )\n"
        f")\n"
    )


def _footprint_text(name: str = "R_0402") -> str:
    return (
        f'(footprint "{name}" (layer "F.Cu")\n'
        f'  (descr "test")\n'
        f'  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask"))\n'
        f")\n"
    )


@pytest.fixture
def other_client(db):
    """A second workspace, for the cross-workspace probes."""
    c = TestClient(app)
    signup_user(c)
    return c


def _upload(client, path: str, filename: str, content: str, **form):
    data = {k: str(v) for k, v in form.items() if v is not None}
    r = client.post(
        path,
        files={"file": (filename, content.encode("utf-8"), "application/octet-stream")},
        data=data,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _upload_symbol(client, entry: str = "R", **form):
    return _upload(
        client, "/api/eda/symbols", f"{entry}.kicad_sym", _symbol_text(entry), **form
    )


def _upload_footprint(client, entry: str = "R_0402", **form):
    return _upload(
        client,
        "/api/eda/footprints",
        f"{entry}.kicad_mod",
        _footprint_text(entry),
        **form,
    )


def _symbol_preview(client, symbol_id):
    return client.get(f"/api/eda/symbols/{symbol_id}/preview.kicad_sch")


def _footprint_preview(client, footprint_id):
    return client.get(f"/api/eda/footprints/{footprint_id}/preview.kicad_pcb")


def _children(node, token: str):
    return [c for c in node[1:] if isinstance(c, list) and sexpr.head(c) == token]


def _placement(doc):
    """The `(symbol (lib_id …) …)` placement — the schematic's direct
    `symbol` child, as opposed to the entries nested in `lib_symbols`."""
    placements = _children(doc, "symbol")
    assert len(placements) == 1, f"expected one placement, got {len(placements)}"
    return placements[0]


# ---------------------------------------------------------------------
# Symbol previews
# ---------------------------------------------------------------------


def test_symbol_preview_is_a_schematic_wrapping_the_stored_entry(authed_client):
    row = _upload_symbol(authed_client, "R")
    r = _symbol_preview(authed_client, row["id"])
    assert r.status_code == 200, r.text

    doc = sexpr.parse(r.text)
    assert sexpr.head(doc) == "kicad_sch"

    lib_symbols = _children(doc, "lib_symbols")
    assert len(lib_symbols) == 1
    entries = _children(lib_symbols[0], "symbol")
    assert [sexpr.entry_name(e) for e in entries] == ["R"]


def test_symbol_preview_keeps_the_stored_geometry(authed_client):
    """The entry is embedded verbatim, so the unit sub-symbol carrying the
    body rectangle has to survive into the preview — that drawing is the
    whole point of rendering it."""
    row = _upload_symbol(authed_client, "R")
    doc = sexpr.parse(_symbol_preview(authed_client, row["id"]).text)

    entry = _children(_children(doc, "lib_symbols")[0], "symbol")[0]
    units = _children(entry, "symbol")
    assert [sexpr.entry_name(u) for u in units] == ["R_0_1"]
    assert _children(units[0], "rectangle"), "body rectangle was dropped"


def test_symbol_preview_lib_id_matches_the_name_inside_the_file(authed_client):
    """Regression guard for the constraint that makes or breaks rendering.

    Upload accepts a `name` form field that renames the row without
    rewriting the stored blob, so `EdaSymbol.name` and the entry name in
    the file diverge here on purpose. KiCanvas resolves a placement by
    looking `lib_id` up in `lib_symbols` **by name**, with no fallback —
    so binding `lib_id` to the row's name instead of the file's would
    render a blank symbol, silently and only for renamed entries.
    """
    row = _upload_symbol(authed_client, "R", name="Resistor 10k")
    assert row["name"] == "Resistor 10k"

    doc = sexpr.parse(_symbol_preview(authed_client, row["id"]).text)
    entry_name = sexpr.entry_name(_children(_children(doc, "lib_symbols")[0], "symbol")[0])
    assert entry_name == "R", "stored entry should keep its in-file name"

    placement = _placement(doc)
    lib_id = next(
        c[1]
        for c in placement[1:]
        if isinstance(c, list) and sexpr.head(c) == "lib_id"
    )
    assert lib_id == entry_name


def test_symbol_preview_placement_carries_a_value_property(authed_client):
    """KiCanvas dereferences `default_instance.value` unguarded when a
    placement has no `Value` property, which throws during parse and
    takes the whole document with it — not just the symbol."""
    row = _upload_symbol(authed_client, "R")
    doc = sexpr.parse(_symbol_preview(authed_client, row["id"]).text)
    assert sexpr.get_property(_placement(doc), "Value") == "R"


def test_symbol_preview_headers(authed_client):
    """`nosniff` + `text/plain` keep attacker-supplied stored text from
    being sniffed into a document on our own origin; `private` keeps a
    shared cache from holding one workspace's geometry for another."""
    row = _upload_symbol(authed_client, "R")
    r = _symbol_preview(authed_client, row["id"])
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=300"


def test_archived_symbol_still_previews(authed_client):
    """The restore flow depends on it: deciding whether to bring an
    archived symbol back means seeing what it is."""
    row = _upload_symbol(authed_client, "R")
    assert authed_client.post(f"/api/eda/symbols/{row['id']}/archive").status_code == 200
    r = _symbol_preview(authed_client, row["id"])
    assert r.status_code == 200, r.text
    assert sexpr.head(sexpr.parse(r.text)) == "kicad_sch"


def test_symbol_preview_unknown_id_404(authed_client):
    r = _symbol_preview(authed_client, uuid.uuid4())
    assert r.status_code == 404
    assert r.json()["code"] == "eda_symbol.not_found"


def test_symbol_preview_is_workspace_isolated(authed_client, other_client):
    row = _upload_symbol(authed_client, "R")
    r = _symbol_preview(other_client, row["id"])
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "eda_symbol.not_found"


# ---------------------------------------------------------------------
# Footprint previews
# ---------------------------------------------------------------------


def test_footprint_preview_is_a_board_wrapping_the_stored_footprint(authed_client):
    row = _upload_footprint(authed_client, "R_0402")
    r = _footprint_preview(authed_client, row["id"])
    assert r.status_code == 200, r.text

    doc = sexpr.parse(r.text)
    assert sexpr.head(doc) == "kicad_pcb"

    footprints = _children(doc, "footprint")
    assert len(footprints) == 1
    assert sexpr.entry_name(footprints[0]) == "R_0402"
    assert _children(footprints[0], "pad"), "pads were dropped"


def test_footprint_preview_declares_the_layers_the_footprint_draws_on(authed_client):
    """KiCanvas resolves every `(layer "…")` through the board's table and
    silently skips what it cannot resolve, so a pad on a layer the wrapper
    forgot to declare renders as nothing."""
    row = _upload_footprint(authed_client, "R_0402")
    doc = sexpr.parse(_footprint_preview(authed_client, row["id"]).text)

    layers = _children(doc, "layers")
    assert len(layers) == 1
    declared = {str(entry[1]) for entry in layers[0][1:] if isinstance(entry, list)}
    # The fixture's pad sits on F.Cu + F.Mask; the table has to cover the
    # rest of the front-side set a real footprint uses too.
    assert {"F.Cu", "B.Cu", "F.Mask", "F.Paste", "F.SilkS", "F.CrtYd", "F.Fab"} <= declared


def test_footprint_preview_headers(authed_client):
    row = _upload_footprint(authed_client, "R_0402")
    r = _footprint_preview(authed_client, row["id"])
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=300"


def test_archived_footprint_still_previews(authed_client):
    row = _upload_footprint(authed_client, "R_0402")
    assert (
        authed_client.post(f"/api/eda/footprints/{row['id']}/archive").status_code == 200
    )
    r = _footprint_preview(authed_client, row["id"])
    assert r.status_code == 200, r.text
    assert sexpr.head(sexpr.parse(r.text)) == "kicad_pcb"


def test_footprint_preview_unknown_id_404(authed_client):
    r = _footprint_preview(authed_client, uuid.uuid4())
    assert r.status_code == 404
    assert r.json()["code"] == "eda_footprint.not_found"


def test_footprint_preview_is_workspace_isolated(authed_client, other_client):
    row = _upload_footprint(authed_client, "R_0402")
    r = _footprint_preview(other_client, row["id"])
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "eda_footprint.not_found"


# ---------------------------------------------------------------------
# Preview documents are not audited
# ---------------------------------------------------------------------


def test_preview_is_a_read_and_writes_no_audit_row(authed_client, db):
    """Guards against someone "fixing" the audit-coverage test by adding a
    log call here: these are GETs, and the audit table is for mutations."""
    from sqlalchemy import func, select

    from app.domain.audit.models import AuditLog

    symbol = _upload_symbol(authed_client, "R")
    footprint = _upload_footprint(authed_client, "R_0402")
    before = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()

    _symbol_preview(authed_client, symbol["id"])
    _footprint_preview(authed_client, footprint["id"])

    after = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()
    assert after == before
