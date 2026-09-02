"""`domain/eda/sexpr.py` — the KiCad s-expression reader/writer.

The fixtures under `tests/fixtures/eda/` are real-shaped KiCad output:
a two-symbol library where the second symbol `(extends "R")` the first
and the first carries graphical unit sub-symbols, and a footprint with
two `(model …)` references. Anything this module gets wrong about that
shape corrupts a user's library on upload, so the round-trip and
"leave what you don't understand alone" properties are pinned here
rather than left to the route tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.eda import sexpr

FIXTURES = Path(__file__).parent / "fixtures" / "eda"
SYMBOL_LIB = (FIXTURES / "two_symbols.kicad_sym").read_text()
FOOTPRINT = (FIXTURES / "footprint_with_models.kicad_mod").read_text()


def _symbol(name: str):
    return dict(sexpr.entries(SYMBOL_LIB))[name]


# ---------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------


def test_parse_returns_nested_lists_and_preserves_quoting():
    node = sexpr.parse('(symbol "R" (in_bom yes))')
    assert node[0] == "symbol"
    assert node[1] == "R"
    # The distinction is the whole point: `yes` is a bare token and "R"
    # is a string. Collapsing them would make emit() write (in_bom "yes"),
    # which KiCad reads as a different value.
    assert isinstance(node[1], sexpr.Quoted)
    assert not isinstance(node[2][1], sexpr.Quoted)


def test_parse_decodes_string_escapes():
    node = sexpr.parse(r'(property "Desc" "a \"quoted\" \\ word\nnext")')
    assert node[2] == 'a "quoted" \\ word\nnext'


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("(symbol", "unbalanced open paren"),
        ("(symbol))", "unbalanced close paren"),
        ('(symbol "R', "unterminated string"),
        ("", "empty document"),
        ("   ", "whitespace only"),
        ("symbol", "atom outside an expression"),
        ('"R"', "string outside an expression"),
        ("(a) (b)", "two top-level expressions"),
    ],
)
def test_parse_rejects_malformed_input(text, reason):
    with pytest.raises(sexpr.SexprError):
        sexpr.parse(text)


def test_parse_rejects_pathological_nesting_instead_of_recursing():
    """A hand-crafted deep file must be a clean SexprError (→ 422), not a
    RecursionError (→ 500). The parser is iterative; this pins the
    explicit depth cap that keeps `emit`'s recursion safe too."""
    with pytest.raises(sexpr.SexprError, match="nesting"):
        sexpr.parse("(" * 5000 + ")" * 5000)


# ---------------------------------------------------------------------
# Round-tripping
# ---------------------------------------------------------------------


@pytest.mark.parametrize("source", [SYMBOL_LIB, FOOTPRINT], ids=["symbol_lib", "footprint"])
def test_emit_parse_round_trip_is_stable(source):
    """Whitespace is normalised on the first pass; everything after that
    is a fixed point. If a second pass differed, some token would be
    changing meaning each time through."""
    once = sexpr.emit(sexpr.parse(source))
    twice = sexpr.emit(sexpr.parse(once))
    assert once == twice
    # And the tree itself is unchanged, not merely the text.
    assert sexpr.parse(once) == sexpr.parse(source)


def test_round_trip_preserves_bare_versus_quoted_tokens():
    text = sexpr.emit(sexpr.parse('(symbol "R" (in_bom yes) (unit "1"))'))
    assert '(in_bom yes)' in text
    assert '(unit "1")' in text


def test_emit_quotes_an_atom_that_would_not_read_back():
    node = ["property", sexpr.Quoted("Key"), "two words"]
    assert sexpr.emit(node) == '(property "Key" "two words")'


# ---------------------------------------------------------------------
# Library entries
# ---------------------------------------------------------------------


def test_entries_lists_top_level_symbols_only():
    found = sexpr.entries(SYMBOL_LIB)
    # "R_0_1" / "R_1_1" are R's graphical units, nested one level deeper —
    # they must not surface as library entries.
    assert [name for name, _ in found] == ["R", "R_Small"]


def test_entries_accepts_a_bare_symbol_node():
    found = sexpr.entries('(symbol "R" (in_bom yes))')
    assert [name for name, _ in found] == ["R"]


def test_entries_rejects_a_footprint_document():
    with pytest.raises(sexpr.SexprError, match="kicad_symbol_lib"):
        sexpr.entries(FOOTPRINT)


def test_entry_name_reads_the_first_string_argument():
    assert sexpr.entry_name(sexpr.parse(FOOTPRINT)) == "R_0402_1005Metric"


def test_entry_name_rejects_a_node_with_no_name():
    with pytest.raises(sexpr.SexprError):
        sexpr.entry_name(sexpr.parse("(symbol)"))


# ---------------------------------------------------------------------
# Renaming
# ---------------------------------------------------------------------


def test_rename_moves_the_entry_and_its_unit_sub_symbols():
    renamed = sexpr.rename(_symbol("R"), "R_US")
    assert sexpr.entry_name(renamed) == "R_US"
    units = [
        sexpr.entry_name(child)
        for child in renamed
        if isinstance(child, list) and sexpr.head(child) == "symbol"
    ]
    # KiCad matches units to their parent by the "NAME_" prefix; leaving
    # them behind gives a symbol that draws as blank.
    assert units == ["R_US_0_1", "R_US_1_1"]


def test_rename_does_not_mutate_the_input():
    original = _symbol("R")
    sexpr.rename(original, "R_US")
    assert sexpr.entry_name(original) == "R"
    units = [
        sexpr.entry_name(child)
        for child in original
        if isinstance(child, list) and sexpr.head(child) == "symbol"
    ]
    assert units == ["R_0_1", "R_1_1"]


def test_rename_leaves_extends_pointing_at_the_original_parent():
    """`(extends "R")` names a DIFFERENT entry. Rewriting it while
    renaming R_Small would silently re-parent the symbol."""
    renamed = sexpr.rename(_symbol("R_Small"), "R_Tiny")
    extends = [c for c in renamed if isinstance(c, list) and sexpr.head(c) == "extends"]
    assert [str(c[1]) for c in extends] == ["R"]


def test_rename_survives_a_round_trip():
    renamed = sexpr.rename(_symbol("R"), "R_US")
    assert sexpr.entry_name(sexpr.parse(sexpr.emit(renamed))) == "R_US"


# ---------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------


def test_get_property_reads_values_including_empty_ones():
    symbol = _symbol("R")
    assert sexpr.get_property(symbol, "Reference") == "R"
    assert sexpr.get_property(symbol, "ki_fp_filters") == "R_*"
    # An unset Footprint is `""`, which must not read back as None —
    # "present but blank" and "absent" mean different things to the
    # phase-5 field generator.
    assert sexpr.get_property(symbol, "Footprint") == ""
    assert sexpr.get_property(symbol, "Nonexistent") is None


def test_set_property_replaces_a_value_and_keeps_its_placement():
    updated = sexpr.set_property(_symbol("R"), "Footprint", "Resistor_SMD:R_0402")
    assert sexpr.get_property(updated, "Footprint") == "Resistor_SMD:R_0402"
    footprint_node = next(
        c
        for c in updated
        if isinstance(c, list) and sexpr.head(c) == "property" and str(c[1]) == "Footprint"
    )
    # The (at …) placement and (effects …) styling ride along untouched,
    # so rewriting a value doesn't move the field on the schematic.
    assert [sexpr.head(c) for c in footprint_node if isinstance(c, list)] == [
        "at",
        "effects",
    ]


def test_set_property_appends_a_new_key_after_the_existing_properties():
    updated = sexpr.set_property(_symbol("R"), "MPN", "RC0402FR-0710KL")
    assert sexpr.get_property(updated, "MPN") == "RC0402FR-0710KL"
    heads = [sexpr.head(c) for c in updated if isinstance(c, list)]
    last_property = len(heads) - 1 - heads[::-1].index("property")
    first_unit = heads.index("symbol")
    # KiCad reads the mandatory four positionally, so a new field has to
    # land after them — and before the graphical units.
    assert last_property < first_unit


def test_set_property_does_not_mutate_the_input():
    original = _symbol("R")
    sexpr.set_property(original, "Footprint", "Lib:Thing")
    assert sexpr.get_property(original, "Footprint") == ""


# ---------------------------------------------------------------------
# 3D model paths
# ---------------------------------------------------------------------


def test_model_paths_lists_every_model_reference():
    assert sexpr.model_paths(sexpr.parse(FOOTPRINT)) == [
        "${KICAD6_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0402_1005Metric.wrl",
        "${KICAD_3RD_PARTY}/3dmodels/R_0402_1005Metric.step",
    ]


def test_rewrite_model_paths_replaces_paths_and_keeps_placement():
    node = sexpr.parse(FOOTPRINT)
    rewritten = sexpr.rewrite_model_paths(node, lambda p: "/eda/" + p.rsplit("/", 1)[-1])
    assert sexpr.model_paths(rewritten) == [
        "/eda/R_0402_1005Metric.wrl",
        "/eda/R_0402_1005Metric.step",
    ]
    model_node = next(
        c for c in rewritten if isinstance(c, list) and sexpr.head(c) == "model"
    )
    # (offset …) (scale …) (rotate …) survive — re-pointing a model must
    # not disturb how it sits on the board.
    assert [sexpr.head(c) for c in model_node if isinstance(c, list)] == [
        "offset",
        "scale",
        "rotate",
    ]


def test_rewrite_model_paths_does_not_mutate_the_input():
    node = sexpr.parse(FOOTPRINT)
    before = sexpr.model_paths(node)
    sexpr.rewrite_model_paths(node, lambda _p: "/replaced")
    assert sexpr.model_paths(node) == before


def test_model_paths_on_a_footprint_without_models_is_empty():
    assert sexpr.model_paths(sexpr.parse('(footprint "X" (layer "F.Cu"))')) == []


def test_unknown_escapes_keep_their_backslash():
    """Windows model paths in vendor footprints ("C:\\Users\\...") carry
    non-canonical escapes; decode must keep the backslash so emit can
    re-escape it — dropping it corrupted stored content (P2 review HIGH)."""
    text = r'(model "C:\Users\foo\bar.step")'
    node = sexpr.parse(text)
    assert str(node[1]) == r"C:\Users\foo\bar.step"

    emitted = sexpr.emit(node)
    assert str(sexpr.parse(emitted)[1]) == r"C:\Users\foo\bar.step"


def test_canonical_escapes_still_round_trip():
    text = '(property "Note" "line1\\nline2\\ttabbed \\"quoted\\"")'
    node = sexpr.parse(text)
    assert str(node[2]) == 'line1\nline2\ttabbed "quoted"'
    assert str(sexpr.parse(sexpr.emit(node))[2]) == str(node[2])


def test_depth_cap_is_generous_for_real_files_but_bounded():
    """Real KiCad files nest ~6 deep; 31 must parse, 41 must not.
    The tight cap is half of the amplification defence (P2 sec HIGH-1)."""
    sexpr.parse("(a " * 30 + "(leaf)" + ")" * 30)
    with pytest.raises(sexpr.SexprError, match="nesting"):
        sexpr.parse("(a " * 40 + "(leaf)" + ")" * 40)


def test_rewrite_model_paths_drops_a_node_when_the_callback_returns_none():
    """The zip importer points a vendor footprint at our own storage and
    has to DROP a `(model …)` whose file the archive didn't carry —
    otherwise KiCad reports a missing model on every board that places
    the footprint."""
    node = sexpr.parse(FOOTPRINT)
    before = sexpr.model_paths(node)
    assert len(before) > 1

    kept = before[0]
    rewritten = sexpr.rewrite_model_paths(node, lambda p: "/eda/kept" if p == kept else None)

    assert sexpr.model_paths(rewritten) == ["/eda/kept"]
    # Non-model children survive, and the input is untouched.
    assert sexpr.model_paths(node) == before
    assert sexpr.entry_name(rewritten) == sexpr.entry_name(node)


def test_rewrite_model_paths_can_drop_every_model():
    node = sexpr.parse(FOOTPRINT)
    rewritten = sexpr.rewrite_model_paths(node, lambda _p: None)
    assert sexpr.model_paths(rewritten) == []
    assert sexpr.head(rewritten) == sexpr.head(node)
