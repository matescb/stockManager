"""Fitting a 3D model onto a footprint from geometry (`model_fit`).

The conventions here were measured against production footprints whose
vendor placement is known good, so they are pinned rather than derived:
KiCad negates the rotate angles, offsets are in the Y-up 3D frame, and a
Y-up STEP needs `rotate (-90 0 0)` to lie on the board.
"""

from __future__ import annotations

import json
import struct

import pytest

from app.domain.eda import model_fit as mf
from app.domain.eda import sexpr


def _footprint(*children: str, attr: str = "smd") -> sexpr.Node:
    body = "\n".join(children)
    return sexpr.parse(f'(footprint "X" (layer "F.Cu") (attr {attr})\n{body}\n)')


def _pad(num: int, x: float, y: float, w: float, h: float, kind: str = "smd") -> str:
    return f'(pad "{num}" {kind} rect (at {x} {y}) (size {w} {h}) (layers "F.Cu"))'


def _courtyard(x0: float, y0: float, x1: float, y1: float) -> str:
    return (
        f'(fp_rect (start {x0} {y0}) (end {x1} {y1}) (layer "F.CrtYd") '
        "(stroke (width 0.05) (type solid)))"
    )


def _glb(doc: dict) -> bytes:
    body = json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    header = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(body))
    return header + struct.pack("<II", len(body), 0x4E4F534A) + body


def _box_glb(lo, hi, **node) -> bytes:
    """A GLB whose single mesh spans `lo`..`hi` (metres) under `node`'s transform."""
    return _glb(
        {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0, **node}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"min": list(lo), "max": list(hi), "count": 8, "type": "VEC3"}],
        }
    )


# ---------------------------------------------------------------------
# Conventions
# ---------------------------------------------------------------------


def test_rotate_minus_90_about_x_stands_a_y_up_model_on_the_board():
    """An 0603 STEP measured from production: 1.6 x 0.45 (Y) x 0.8 (Z),
    placed by its vendor with `rotate (-90 0 0)`, renders 1.6 x 0.8 with
    its height 0..0.45 — +Y maps to +Z."""
    bounds = ((-0.8, 0.0, -0.4), (0.8, 0.45, 0.4))
    lo, hi = mf.place(bounds, mf.Placement(offset=(0, 0, 0), rotate=(-90, 0, 0)))
    assert hi[0] - lo[0] == pytest.approx(1.6)
    assert hi[1] - lo[1] == pytest.approx(0.8)
    assert (lo[2], hi[2]) == pytest.approx((0.0, 0.45))


def test_offset_is_added_in_the_3d_frame_after_rotation():
    bounds = ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0))
    lo, hi = mf.place(bounds, mf.Placement(offset=(1, 2, 3), rotate=(0, 0, 0)))
    assert lo == pytest.approx((1, 2, 3))
    assert hi == pytest.approx((2, 4, 6))


# ---------------------------------------------------------------------
# GLB bounds
# ---------------------------------------------------------------------


def test_glb_bounds_reads_accessor_extents_in_millimetres():
    lo, hi = mf.glb_bounds(_box_glb((-0.001, -0.002, 0), (0.001, 0.002, 0.003)))
    assert lo == pytest.approx((-1, -2, 0))
    assert hi == pytest.approx((1, 2, 3))


def test_glb_bounds_applies_node_translation():
    lo, hi = mf.glb_bounds(_box_glb((0, 0, 0), (0.001, 0.001, 0.001), translation=[0.01, 0, 0]))
    assert lo[0] == pytest.approx(10)
    assert hi[0] == pytest.approx(11)


def test_glb_bounds_applies_a_column_major_matrix():
    matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0.005, 0, 1]  # translate y by 5 mm
    lo, hi = mf.glb_bounds(_box_glb((0, 0, 0), (0.001, 0.001, 0.001), matrix=matrix))
    assert (lo[1], hi[1]) == pytest.approx((5, 6))


def test_glb_bounds_survives_a_node_cycle():
    doc = {
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "children": [1]}, {"children": [0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"min": [0, 0, 0], "max": [0.001, 0.001, 0.001]}],
    }
    _, hi = mf.glb_bounds(_glb(doc))
    assert hi == pytest.approx((1, 1, 1))


@pytest.mark.parametrize("blob", [b"", b"not a glb at all", _glb({"nodes": []})])
def test_glb_without_geometry_is_a_fit_error(blob):
    with pytest.raises(mf.FitError):
        mf.glb_bounds(blob)


# ---------------------------------------------------------------------
# Footprint target
# ---------------------------------------------------------------------


def test_target_prefers_the_courtyard_and_flips_y():
    node = _footprint(_pad(1, -1, 0, 0.5, 0.5), _pad(2, 1, 0, 0.5, 0.5), _courtyard(-2, -1, 2, 3))
    target = mf.footprint_target(node)
    assert target.box == pytest.approx((-2, -3, 2, 1))
    assert target.smd is True


def test_target_falls_back_to_pads_and_reads_through_hole():
    node = _footprint(
        _pad(1, 0, 0, 1.5, 1.5, "thru_hole"),
        _pad(2, 2.54, 0, 1.5, 1.5, "thru_hole"),
        attr="through_hole",
    )
    target = mf.footprint_target(node)
    assert target.box == pytest.approx((-0.75, -0.75, 3.29, 0.75))
    assert target.smd is False


def test_rotated_pads_are_measured_rotated():
    node = _footprint('(pad "1" smd rect (at 0 0 90) (size 4 1) (layers "F.Cu"))')
    x0, y0, x1, y1 = mf.footprint_target(node).box
    assert (x1 - x0, y1 - y0) == pytest.approx((1, 4))


def test_a_footprint_with_nothing_to_measure_is_a_fit_error():
    with pytest.raises(mf.FitError):
        mf.footprint_target(_footprint())


# ---------------------------------------------------------------------
# The fit
# ---------------------------------------------------------------------

_QFN = _footprint(
    _pad(1, -1.45, 0, 0.8, 2.5), _pad(2, 1.45, 0, 0.8, 2.5), _courtyard(-1.9, -1.9, 1.9, 1.9)
)


def test_a_y_up_self_centred_snapeda_model_is_laid_flat_on_the_board():
    """The production failure: a 3x3x1 QFN modelled Y-up around its own
    origin stood on edge, half inside the board, at the identity."""
    bounds = ((-1.5, -0.5, -1.5), (1.5, 0.5, 1.5))
    result = mf.fit(bounds, mf.footprint_target(_QFN))

    lo, hi = mf.place(bounds, result.placement)
    assert result.placement.rotate == (-90, 0, 0)
    assert (lo[2], hi[2]) == pytest.approx((0, 1))
    assert (lo[0] + hi[0], lo[1] + hi[1]) == pytest.approx((0, 0))


def test_a_model_that_already_sits_on_its_footprint_is_left_alone():
    bounds = ((-1.5, -1.5, 0.0), (1.5, 1.5, 0.9))
    result = mf.fit(bounds, mf.footprint_target(_QFN))
    assert result.placement == mf.Placement(offset=(0, 0, 0), rotate=(0, 0, 0))
    assert result.recentred is False


def test_a_model_elongated_across_its_footprint_is_turned_90_degrees():
    target = mf.footprint_target(_footprint(_courtyard(-3, -1, 3, 1)))
    bounds = ((-1.0, -3.0, 0.0), (1.0, 3.0, 1.0))
    result = mf.fit(bounds, target)
    lo, hi = mf.place(bounds, result.placement)
    assert (hi[0] - lo[0], hi[1] - lo[1]) == pytest.approx((6, 2))
    assert result.placement.rotate[2] in (90, -90)


def test_a_floating_model_is_brought_down_onto_the_board():
    bounds = ((-1.5, -1.5, 40.0), (1.5, 1.5, 41.0))
    result = mf.fit(bounds, mf.footprint_target(_QFN))
    lo, _ = mf.place(bounds, result.placement)
    assert lo[2] == pytest.approx(0)


def test_a_through_hole_model_keeps_pins_below_the_board():
    """A THT model drawn with the board at z=0 and pins below it must not
    be lifted so its pin tips sit on the board."""
    node = _footprint(
        _pad(1, 0, 0, 1.5, 1.5, "thru_hole"),
        _pad(2, 5.08, 0, 1.5, 1.5, "thru_hole"),
        _courtyard(-1.5, -3, 6.6, 3),
        attr="through_hole",
    )
    bounds = ((-1.0, -2.5, -3.5), (6.1, 2.5, 10.0))
    result = mf.fit(bounds, mf.footprint_target(node))
    assert result.placement.offset[2] == 0


def test_a_flat_model_has_no_extent_to_fit():
    with pytest.raises(mf.FitError):
        mf.fit(((0, 0, 0), (1, 1, 0)), mf.footprint_target(_QFN))


# ---------------------------------------------------------------------
# Stored form
# ---------------------------------------------------------------------


def test_placement_round_trips_through_json():
    placement = mf.Placement(offset=(0.1, -2.0, 0.5), rotate=(-90, 0, 90))
    assert mf.Placement.from_json(placement.as_json()) == placement


@pytest.mark.parametrize(
    "stored", [None, "x", {}, {"offset": [0, 0]}, {"offset": [0, 0, 0], "rotate": ["a", 0, 0]}]
)
def test_a_malformed_stored_placement_reads_as_none(stored):
    assert mf.Placement.from_json(stored) is None


def test_negative_zero_is_stored_as_zero():
    stored = mf.Placement(offset=(-0.0, -1e-9, 0), rotate=(0, 0, 0)).as_json()
    assert stored["offset"] == [0.0, 0.0, 0.0]
    assert str(stored["offset"][0]) == "0.0"
