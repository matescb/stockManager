"""SM-009 parser against the ngSPICE inventory exports, read IN PLACE.

`/mnt/data/WORK/ngSPICE/exports/inventory/` holds `export-model` files
for the inventory parts plus `manifest.json` (ngSPICE commit 8c07aaf).
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

from app.domain.eda import spice_model

INVENTORY = Path("/mnt/data/WORK/ngSPICE/exports/inventory")
MANIFEST = INVENTORY / "manifest.json"

pytestmark = pytest.mark.skipif(not MANIFEST.is_file(), reason="ngSPICE inventory not present")

# The manifest spells builds in words, the header as the probe's token.
_BUILD_TOKEN = {"stock": "stock", "patched": "patched", "patched with runtime": "patched+runtime"}


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

    header = spice_model.parse_ng010_header(text)
    assert header is not None
    assert header.catalogue_entry == entry["model"]
    assert header.converted and all(len(sha) == 64 for _, sha in header.converted)
    assert header.build_needs == _BUILD_TOKEN[entry["build"]]
    assert header.grade and header.grade.startswith("valid_for=")
    assert len(header.pins) == len(entry["ports"])

    model = spice_model.find_model(spice_model.parse_models(text), entry["model"])
    assert model is not None
    assert list(model.ports) == entry["ports"]
    assert model.kind == ("subckt" if entry["kind"] == "subckt" else "model")


def test_corrected_variants_parse():
    for entry in _exported():
        corrected = entry.get("corrected_export")
        if not corrected:
            continue
        raw = (INVENTORY / corrected["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == corrected["sha256"]
        header = spice_model.parse_ng010_header(raw.decode("utf-8", errors="replace"))
        ids = [c["id"] for c in corrected["corrections"]]
        assert [c.split(" ", 1)[0] for c in header.corrections] == ids


def test_every_manifest_kind_maps_to_a_binding_kind():
    parts = json.loads(MANIFEST.read_text(encoding="utf-8"))["parts"]
    kinds = {p["kind"] for p in parts}
    assert kinds <= {"subckt", "model card", "native primitive with parameters", "none"}
