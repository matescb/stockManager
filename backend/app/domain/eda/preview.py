"""Synthetic KiCad documents that make a stored entry viewable in a browser.

`api/routes/eda.py` serves two preview endpoints and everything they
return is built here. This is a *rendering* concern only — nothing in
this module is ever stored, and nothing KiCad or the PCM consumes comes
from here. `pcm.py` builds the real artifacts; the wrappers there and
the wrappers here look superficially similar and are not interchangeable
(see "Why not the PCM wrapper" below).

The viewer cannot read what we store
------------------------------------

KiCanvas — the 2D viewer the CAD tab embeds — reads exactly four
document types: `.kicad_sch`, `.kicad_pcb`, `.kicad_wks` and
`.kicad_pro`. It has **no reader for `.kicad_sym` or `.kicad_mod`**,
which is precisely what this domain stores. Pointing it at either yields
"Unknown file type".

What it does have is a complete symbol parser (`LibSymbol`) and a
complete footprint parser (`Footprint`) — it just only ever reaches them
through a schematic or a board. So the fix is to hand it the container
it does understand, with our stored bytes embedded verbatim:

* a symbol entry goes into a one-symbol `(kicad_sch …)`, inside
  `(lib_symbols …)`, with a single placement referencing it;
* a footprint document goes into a one-footprint `(kicad_pcb …)`.

The stored bytes are never re-emitted, only wrapped, so what the user
sees is the geometry we actually hold rather than a round-trip of it.

Pinned from both ends
---------------------

A document KiCanvas cannot parse renders **blank** — no exception, no
console error, nothing in Sentry. Neither half of that can be caught by
reading code, so the wrapping is pinned by a pair of tests that fail
loudly instead:

* `tests/test_eda_preview_fixtures.py` checks the documents this module
  builds against copies checked in at `tests/fixtures/eda/preview/`, so a
  change here has to be deliberate;
* `web/src/components/eda/__tests__/kicanvasContract.test.ts` parses those
  same files with KiCanvas's real parsers, pinned at the commit the app
  ships, so a viewer bump that stops accepting them fails too.

If you change anything below, refresh the fixtures (the backend test says
how) and then run the vitest test — it is the half that decides whether
the new output still draws.

Two constraints that are not obvious
------------------------------------

Both were found by running KiCanvas's own parsers against this repo's
fixtures, and both will silently produce a blank or broken preview if
undone:

1. **`lib_id` must equal the entry's name exactly.**
   `SchematicSymbol.lib_symbol` resolves by looking the `lib_id` up in
   `lib_symbols` by name — there is no library-nickname handling and no
   fallback. A mismatch resolves to nothing and the symbol draws blank.
   The name is therefore read out of the *stored bytes*, never off the
   row: `upload_entry` takes an optional `name` that overrides the row's
   name without rewriting the blob, so `EdaSymbol.name` and the name
   inside the file legitimately diverge. (A later rename *does* rewrite
   the blob — `service._rewrite_stored_entry_name` — but the upload path
   does not, which is enough to make the row untrustworthy here.)

2. **The placement needs a `Value` property.**
   KiCanvas dereferences `this.default_instance.value` unguarded when a
   placement has no `Value` property of its own, and `default_instance`
   is undefined unless the file carries a `(default_instance …)` node.
   A placement without one throws *during parse*, taking the whole
   document with it — not just that symbol.

Known limitation: `(extends "PARENT")` derived symbols render blank.
KiCanvas has no `extends` support whatsoever at the pinned commit (the
token appears nowhere in its `LibSymbol` parse spec), so pulling the
parent into `lib_symbols` would not help — there is nothing to fix on
this side.

Why not the PCM wrapper
-----------------------

`pcm.py` also wraps stored symbol entries, into `(kicad_symbol_lib …)`.
That is the format KiCad itself installs and it is the wrong one here —
it is one of the types KiCanvas cannot read. The two wrappers share a
shape but not a purpose, and merging them would couple a viewer
workaround to the artifact contract KiCad depends on.
"""
from __future__ import annotations

from app.domain.eda import sexpr

__all__ = ["symbol_document", "footprint_document"]

# Format versions KiCanvas's parsers accept. Both are read as plain
# numbers and only gate a handful of upstream compatibility branches;
# these are the versions KiCad 7/8 write, which is the era the stored
# entries come from.
SCHEMATIC_FORMAT_VERSION = "20231120"
BOARD_FORMAT_VERSION = "20221018"
GENERATOR = "stockmanager"

# Fixed placement coordinates. The viewer zooms to fit whatever it finds,
# so the absolute position is arbitrary — it only has to be on the sheet.
_ORIGIN = "100 100 0"

# Deterministic UUIDs. A preview document is regenerated on every request
# and nothing downstream correlates on these, so stable values keep the
# response byte-identical for unchanged content (which is what makes the
# short private cache window on the route worth having).
_SHEET_UUID = "00000000-0000-4000-8000-000000000001"
_PLACEMENT_UUID = "00000000-0000-4000-8000-000000000002"

# The layer table a footprint is drawn against, mirroring KiCad's own
# default two-layer board.
#
# This is not decoration. KiCanvas builds its render layers by walking a
# fixed list and SKIPPING any physical layer the board does not declare
# (`viewers/board/layers.ts::LayerSet`), so geometry on an undeclared
# layer has nowhere to draw and silently never appears. Copper is worse
# than silent: pad visibility resolves the F.Cu / B.Cu layers with a
# non-null assertion, so dropping either throws instead of merely hiding.
#
# Hence the full standard set rather than the few layers a given fixture
# happens to use. Through-hole pads name the `*.Cu` / `*.Mask` wildcards,
# which KiCanvas expands to BOTH sides
# (`viewers/board/painter.ts::PadPainter.layers_for`), so a back-side
# entry is reachable from any footprint, not just an obviously back-side
# one. `tests/test_eda_preview_fixtures.py` asserts no fixture references
# a layer this table omits, and the vitest contract test re-checks it
# against KiCanvas's real parser.
_BOARD_LAYERS = """\
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (32 "B.Adhes" user "B.Adhesive")
    (33 "F.Adhes" user "F.Adhesive")
    (34 "B.Paste" user)
    (35 "F.Paste" user)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (40 "Dwgs.User" user "User.Drawings")
    (41 "Cmts.User" user "User.Comments")
    (42 "Eco1.User" user "User.Eco1")
    (43 "Eco2.User" user "User.Eco2")
    (44 "Edge.Cuts" user)
    (45 "Margin" user)
    (46 "B.CrtYd" user "B.Courtyard")
    (47 "F.CrtYd" user "F.Courtyard")
    (48 "B.Fab" user)
    (49 "F.Fab" user)"""


def _quote(value: str) -> str:
    """Escape `value` for use inside a quoted s-expression atom."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def symbol_document(entry: bytes, *, name: str | None = None) -> str:
    """A one-symbol `.kicad_sch` wrapping the stored `(symbol …)` entry.

    `entry` is the stored canonical bare entry, embedded verbatim.
    `name` overrides the `lib_id`/`Value` text for callers that already
    know it; when omitted the name is read out of `entry` itself, which
    is the only source that is guaranteed to match (see the module
    docstring). Raises `sexpr.SexprError` if the stored bytes do not
    parse as a `(symbol …)` node.
    """
    text = entry.decode("utf-8")
    entry_name = name if name is not None else sexpr.entry_name(sexpr.parse(text))
    quoted = _quote(entry_name)
    return (
        f"(kicad_sch (version {SCHEMATIC_FORMAT_VERSION}) (generator {GENERATOR})\n"
        f'  (uuid "{_SHEET_UUID}")\n'
        f'  (paper "A4")\n'
        f"  (lib_symbols\n"
        f"{text.rstrip()}\n"
        f"  )\n"
        f'  (symbol (lib_id "{quoted}") (at {_ORIGIN}) (unit 1)\n'
        f"    (in_bom yes) (on_board yes)\n"
        f'    (uuid "{_PLACEMENT_UUID}")\n'
        f'    (property "Reference" "?" (at {_ORIGIN})\n'
        f"      (effects (font (size 1.27 1.27)) hide)\n"
        f"    )\n"
        f'    (property "Value" "{quoted}" (at {_ORIGIN})\n'
        f"      (effects (font (size 1.27 1.27)) hide)\n"
        f"    )\n"
        f"  )\n"
        f'  (sheet_instances (path "/" (page "1")))\n'
        f")\n"
    )


def footprint_document(document: bytes) -> str:
    """A one-footprint `.kicad_pcb` wrapping the stored `.kicad_mod`.

    The stored document is already a complete `(footprint …)` node, which
    is exactly the shape a board carries inline, so it is embedded
    verbatim with no parse at all — the only thing it gains is the board
    header and the layer table its geometry is drawn against.
    """
    return (
        f"(kicad_pcb (version {BOARD_FORMAT_VERSION}) (generator {GENERATOR})\n"
        f"  (general (thickness 1.6))\n"
        f'  (paper "A4")\n'
        f"  (layers\n"
        f"{_BOARD_LAYERS}\n"
        f"  )\n"
        f"{document.decode('utf-8').rstrip()}\n"
        f")\n"
    )
