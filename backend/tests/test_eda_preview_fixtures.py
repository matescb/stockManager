"""The preview documents checked in for the frontend contract test.

`tests/fixtures/eda/preview/` holds the exact output of
`domain/eda/preview.py` for three source fixtures. Those files are read by
a **vitest** test — `web/src/components/eda/__tests__/kicanvasContract.test.ts`
— which parses them with KiCanvas's own parsers, pinned at the same commit
as the bundle shipped in `web/public/kicanvas/`.

That makes the fixtures a contract between two languages, and this module
is the half that keeps them honest:

* if a builder in `preview.py` changes, this test fails and tells you to
  refresh the fixtures;
* if KiCanvas is bumped and its parsers stop accepting them, the vitest
  test fails.

Neither failure is a false alarm — a preview that KiCanvas cannot parse
renders blank, with no error anywhere, which is precisely the failure this
pair exists to make loud.

Refresh after an intentional builder change:

    UPDATE_PREVIEW_FIXTURES=1 python -m pytest tests/test_eda_preview_fixtures.py

then re-run the vitest contract test, which is the half that decides
whether the new output still renders.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.domain.eda import preview, storage

FIXTURES = Path(__file__).parent / "fixtures" / "eda"
PREVIEW_DIR = FIXTURES / "preview"

# source fixture -> generated preview document.
#
# Chosen for coverage of what actually breaks rendering, not for variety:
#
# * `symbol_R.kicad_sym` — a real KiCad resistor: unit sub-symbols
#   (`R_0_1` body graphics, `R_1_1` pins) that only survive if the entry is
#   embedded verbatim, and a name the placement's `lib_id` has to match. A
#   one-symbol library rather than `two_symbols.kicad_sym` because that is
#   the shape the upload lane accepts — a multi-symbol file is a 422 there,
#   so canonicalising one would exercise a path production never takes.
# * `footprint_with_models.kicad_mod` — front-side SMD pads with paste and
#   mask, plus `(model …)` nodes the wrapper must leave alone.
# * `footprint_back_and_through_hole.kicad_mod` — the layer-coverage case:
#   B.Cu pads, a through-hole pad on the `*.Cu` / `*.Mask` wildcards, and
#   graphics on B.SilkS, B.Fab, B.CrtYd, Edge.Cuts and Dwgs.User. KiCanvas
#   builds its render layers by intersecting a fixed list with the layers
#   the BOARD declares, so a layer missing from the synthetic table means
#   geometry that silently never draws.
_SYMBOL_CASES = [("symbol_R.kicad_sym", "symbol_R.kicad_sch")]
_FOOTPRINT_CASES = [
    ("footprint_with_models.kicad_mod", "footprint_front_smd.kicad_pcb"),
    (
        "footprint_back_and_through_hole.kicad_mod",
        "footprint_back_and_through_hole.kicad_pcb",
    ),
]


def _symbol_preview(source: Path) -> str:
    """The preview for `source`, built exactly as the route builds it.

    The upload lane's canonicaliser runs first, because what the route
    wraps is the *stored* form — not the file as uploaded. A fixture
    generated from the raw bytes would drift from production the moment
    canonicalisation changed anything.
    """
    _name, stored = storage.canonical_symbol(source.read_bytes())
    return preview.symbol_document(stored)


def _footprint_preview(source: Path) -> str:
    _name, stored = storage.canonical_footprint(source.read_bytes())
    return preview.footprint_document(stored)


_BUILDERS = {"kicad_sch": _symbol_preview, "kicad_pcb": _footprint_preview}


def _check(source_name: str, generated_name: str) -> None:
    source = FIXTURES / source_name
    target = PREVIEW_DIR / generated_name
    expected = _BUILDERS[generated_name.rsplit(".", 1)[-1]](source)

    if os.environ.get("UPDATE_PREVIEW_FIXTURES"):
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(expected, encoding="utf-8")
        return

    assert target.is_file(), (
        f"{target} is missing. Generate it with "
        f"UPDATE_PREVIEW_FIXTURES=1 python -m pytest {Path(__file__).name}"
    )
    assert target.read_text(encoding="utf-8") == expected, (
        f"{generated_name} no longer matches what preview.py builds.\n"
        f"If the builder change was intentional, refresh with\n"
        f"  UPDATE_PREVIEW_FIXTURES=1 python -m pytest {Path(__file__).name}\n"
        f"and then re-run web/src/components/eda/__tests__/kicanvasContract.test.ts "
        f"— it is the half that proves KiCanvas still parses the result."
    )


@pytest.mark.parametrize("source,generated", _SYMBOL_CASES + _FOOTPRINT_CASES)
def test_checked_in_preview_fixtures_match_the_builders(source: str, generated: str):
    _check(source, generated)


def test_the_layer_table_covers_every_layer_the_fixtures_reference():
    """No stored footprint may name a layer the synthetic board omits.

    KiCanvas creates a render layer only when the board declares one with
    that canonical name (`viewers/board/layers.ts::LayerSet`), so a pad or
    graphic on an undeclared layer has nowhere to draw. Worse for copper:
    pad visibility dereferences the F.Cu/B.Cu layers with a non-null
    assertion, so omitting those throws rather than merely hiding.

    Asserted here rather than only in vitest because this is a property of
    `preview._BOARD_LAYERS`, and it should fail in whichever suite runs
    first.
    """
    from app.domain.eda import sexpr

    declared = {
        str(entry[1])
        for entry in sexpr.parse(
            f"(layers\n{preview._BOARD_LAYERS}\n)"
        )[1:]
        if isinstance(entry, list)
    }

    for source_name, _ in _FOOTPRINT_CASES:
        node = sexpr.parse((FIXTURES / source_name).read_text())
        for referenced in _layer_tokens(node, sexpr):
            for name in _expand(referenced):
                assert name in declared, (
                    f"{source_name} draws on {referenced!r}, which "
                    f"preview._BOARD_LAYERS does not declare"
                )


def _layer_tokens(node, sexpr) -> set[str]:
    """Every layer name a footprint's pads and graphics reference."""
    found: set[str] = set()
    for child in node:
        if not isinstance(child, list):
            continue
        head = sexpr.head(child)
        if head == "layer":
            found.update(str(a) for a in child[1:] if isinstance(a, str))
        elif head == "layers":
            found.update(str(a) for a in child[1:] if isinstance(a, str))
        found.update(_layer_tokens(child, sexpr))
    return found


def _expand(layer: str) -> list[str]:
    """`*.Cu` and friends mean both sides — the same expansion KiCanvas's
    `PadPainter.layers_for` applies."""
    if layer.startswith("*."):
        suffix = layer[2:]
        return [f"F.{suffix}", f"B.{suffix}"]
    return [layer]
