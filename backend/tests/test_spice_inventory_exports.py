"""SM-009 parser against the ngSPICE inventory exports, read IN PLACE.

`/mnt/data/WORK/ngSPICE/exports/inventory/` holds `export-model` files
for the inventory parts plus `manifest.json` (schema
`ngspice-inventory-manifest/1`, exports `ngspice-export/1`; ngSPICE
8e97fed, manifest 4a3f7b6).
The `.lib` files contain vendor model text and are git-ignored there;
they are never copied into this repo. The test skips wherever the
directory is absent (CI, other machines).

Checked for every exported part: the NG-010 header parses (entry name,
converted-file sha256, build, grade), the build named in the header is
the manifest's, the file's own sha256 is the manifest's, the manifest's
model is a top-level model in the file, and its ports are the
manifest's ports in order.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.domain.eda import spice_header, spice_model

INVENTORY = Path("/mnt/data/WORK/ngSPICE/exports/inventory")
MANIFEST = INVENTORY / "manifest.json"

pytestmark = pytest.mark.skipif(not MANIFEST.is_file(), reason="ngSPICE inventory not present")

_KIND_TO_MODEL = {"subckt": "subckt", "model_card": "model"}


def _exported() -> list[dict]:
    if not MANIFEST.is_file():
        return []
    parts = json.loads(MANIFEST.read_text(encoding="utf-8"))["parts"]
    return [p for p in parts if p.get("export")]


@pytest.mark.parametrize("entry", _exported(), ids=lambda e: e["mpn"])
def test_export_parses_and_matches_manifest(entry):
    raw = (INVENTORY / entry["export"]["file"]).read_bytes()
    text = raw.decode("utf-8", errors="replace")
    assert hashlib.sha256(raw).hexdigest() == entry["export"]["sha256"]

    header = spice_header.parse_ng010_header(text)
    assert header is not None
    assert header.catalogue_entry == entry["model"]
    assert header.converted and all(len(sha) == 64 for _, sha in header.converted)
    assert header.export_schema == "ngspice-export/1"
    assert header.kind == entry["kind"]
    assert header.build_needs == entry["build"]
    assert header.grade and header.grade.startswith("valid_for=")
    assert len(header.pins) == len(entry["ports"])

    model = spice_model.find_model(spice_model.parse_models(text), entry["model"])
    assert model is not None
    assert list(model.ports) == entry["ports"]
    assert model.kind == _KIND_TO_MODEL[entry["kind"]]

    table = [
        {
            "port": p.port,
            "function": p.function,
            "on_symbol": p.on_symbol,
            "internally_unconnected": p.internally_unconnected,
            "function_source": p.source,
        }
        for p in header.port_table
    ]
    assert table == entry["port_table"]
    assert header.internally_unconnected == (entry["internally_unconnected_ports"] or [])
    expected_channels = {
        k: (v or {}).get("count") for k, v in (entry.get("channels") or {}).items()
    }
    assert header.channels == ({"package": None, "model": None} | expected_channels)
    assert header.valid_for_basis == (entry["grade"].get("valid_for_basis") or {})
    assert header.match["confidence"] == entry["match"]["confidence"]
    assert header.match["model"] == entry["match"]["matched_name"]


def test_corrected_variants_parse():
    for entry in _exported():
        corrected = entry.get("corrected_export")
        if not corrected:
            continue
        raw = (INVENTORY / corrected["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == corrected["sha256"]
        header = spice_header.parse_ng010_header(raw.decode("utf-8", errors="replace"))
        ids = [c["id"] for c in corrected["corrections"]]
        assert [c.split(" ", 1)[0] for c in header.corrections] == ids


def test_manifest_schema_and_file_less_parts_fit_set_spice_binding():
    """Every file-less manifest part is accepted by the binding's checks."""
    from app.domain.eda import spice_binding

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema"] == "ngspice-inventory-manifest/1"
    assert manifest["export_schema"] == "ngspice-export/1"
    parts = manifest["parts"]
    assert {p["kind"] for p in parts} <= {"subckt", "model_card", "native_primitive", "none"}
    for p in parts:
        if p.get("fallback"):
            spice_binding._check_fallback_token(p["fallback"]["token"])
        if p["kind"] == "native_primitive":
            record = spice_binding._native_record(p["native"], "native_primitive")
            assert record["letter"] in spice_model.NATIVE_PORTS
