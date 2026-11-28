"""Work out where a 3D model sits on a footprint when nobody said.

A footprint's `(model …)` node carries its own `offset` / `rotate` /
`scale`. A model linked on the CAD tab — or a SnapEDA STEP whose
`.kicad_mod` names no model at all — has no node, so it used to be
appended at the identity placement. Vendors don't model to KiCad's
convention: many SnapEDA STEPs are Y-up and centred on their own origin,
so at the identity they stood on edge, half inside the board.

This module picks a placement from geometry alone:

* the model's axis-aligned bounding box, read from the GLB that
  `preview3d` already builds from the STEP (no new dependency — the
  accessor `min`/`max` the glTF writer records, pushed through the node
  transforms);
* the footprint's target box — the courtyard if it has one, else the
  pads — and whether it is SMD or through-hole.

Every axis-aligned orientation is tried twice: as the vendor placed it
relative to its own origin, and re-centred on the target (bottom on the
board for SMD). The best overlap wins, with a small bias towards keeping
the vendor's origin and towards the simpler rotation, so a model that
already fits is left exactly where it was.

What geometry cannot tell: which way pin 1 faces on a part that is
symmetric in its bounding box (0° vs 180°). The result is a starting
point, not a guarantee — the placement is stored on the link row where
it can be corrected.

Conventions, measured against production footprints whose vendor
placement is known good: KiCad applies
`M = T(offset) · Rz(-rz) · Ry(-ry) · Rx(-rx) · S(scale)` in the 3D frame,
which is the footprint frame with Y flipped (Y up), in mm. cascadio's GLB
keeps the STEP's own axes, in metres.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from typing import Any

from app.domain.eda import sexpr

Vec = tuple[float, float, float]
Box = tuple[Vec, Vec]  # (min, max)
Matrix = list[list[float]]

_IDENTITY_SCALE: Vec = (1.0, 1.0, 1.0)

# Preference order. Bounding boxes can't tell 90 from -90 (or a model
# from itself upside down), so ties go to the earlier, simpler rotation;
# -90 about X comes first among the X turns because that is what turns a
# Y-up model the right way up in KiCad's sign convention.
_ORIENTATIONS: tuple[Vec, ...] = (
    (0, 0, 0),
    (-90, 0, 0),
    (0, 0, 90),
    (-90, 0, 90),
    (90, 0, 0),
    (90, 0, 90),
    (0, 90, 0),
    (0, -90, 0),
    (0, 90, 90),
    (0, -90, 90),
    (180, 0, 0),
    (180, 0, 90),
)

# Re-centring a model the vendor placed costs this much overlap: a
# connector that legitimately overhangs its pads must keep its origin.
_RECENTRE_BIAS = 0.05
# A model that already sits on the board covering at least this much of
# the target is left exactly as the vendor placed it.
_KEEP_AS_PLACED_IOU = 0.5
# Standing a model on its X axis: no vendor in production models that
# way (Z-up and Y-up only), and in a symmetric bounding box it is the
# orientation most likely to be a coincidence.
_X_UP_PENALTY = 0.1
_PREFERENCE_STEP = 0.001
# A model whose bottom is further than this from the board is not
# "sitting on" it for the as-placed candidate.
_SMD_Z_TOLERANCE = 0.25
_THT_MAX_BELOW_FRACTION = 0.6

_GLB_MAGIC = b"glTF"
_JSON_CHUNK = 0x4E4F534A
_METRES_TO_MM = 1000.0


class FitError(ValueError):
    """The inputs carry no usable geometry."""


@dataclass(frozen=True)
class Placement:
    offset: Vec
    rotate: Vec
    scale: Vec = _IDENTITY_SCALE

    def as_json(self) -> dict[str, list[float]]:
        return {
            "offset": [_round(v) for v in self.offset],
            "rotate": [_round(v) for v in self.rotate],
            "scale": [_round(v) for v in self.scale],
        }

    @classmethod
    def from_json(cls, data: Any) -> Placement | None:
        """Lenient read of a stored placement; None if it isn't one."""
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                offset=_vec(data["offset"]),
                rotate=_vec(data["rotate"]),
                scale=_vec(data.get("scale", _IDENTITY_SCALE)),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class Target:
    """Where the model should land, in the 3D frame (Y up, mm)."""

    box: tuple[float, float, float, float]  # x0, y0, x1, y1
    smd: bool


@dataclass(frozen=True)
class Fit:
    placement: Placement
    score: float  # XY overlap (IoU) of the placed model with the target
    recentred: bool


# ---------------------------------------------------------------------
# Model bounds from a GLB
# ---------------------------------------------------------------------


def glb_bounds(glb: bytes) -> Box:
    """Axis-aligned bounds of every mesh in a GLB, in mm.

    Uses the POSITION accessors' `min` / `max`, which glTF requires, so
    no buffer is decoded. Each mesh box is pushed through its node's
    world transform corner by corner.
    """
    if len(glb) < 20 or glb[:4] != _GLB_MAGIC:
        raise FitError("not a GLB")
    length, chunk_type = struct.unpack_from("<II", glb, 12)
    if chunk_type != _JSON_CHUNK:
        raise FitError("GLB has no JSON chunk")
    try:
        doc = json.loads(glb[20 : 20 + length])
    except ValueError as exc:
        raise FitError("GLB JSON chunk is unreadable") from exc

    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for corner in _mesh_corners(doc):
        for i in range(3):
            lo[i] = min(lo[i], corner[i])
            hi[i] = max(hi[i], corner[i])
    if not all(math.isfinite(v) for v in (*lo, *hi)):
        raise FitError("GLB has no mesh bounds")
    return (
        (lo[0] * _METRES_TO_MM, lo[1] * _METRES_TO_MM, lo[2] * _METRES_TO_MM),
        (hi[0] * _METRES_TO_MM, hi[1] * _METRES_TO_MM, hi[2] * _METRES_TO_MM),
    )


def _mesh_corners(doc: dict) -> list[Vec]:
    nodes = doc.get("nodes", [])
    meshes = doc.get("meshes", [])
    accessors = doc.get("accessors", [])
    out: list[Vec] = []
    # Iterative walk with a visited set: a malformed GLB with a node
    # cycle must not recurse forever.
    scenes = doc.get("scenes") or [{"nodes": list(range(len(nodes)))}]
    scene = scenes[doc.get("scene", 0)] if doc.get("scene", 0) < len(scenes) else scenes[0]
    stack: list[tuple[int, Matrix]] = [(root, _IDENTITY4) for root in scene.get("nodes", [])]
    seen: set[int] = set()
    while stack:
        index, parent = stack.pop()
        if index in seen or not 0 <= index < len(nodes):
            continue
        seen.add(index)
        node = nodes[index]
        world = _matmul4(parent, _node_matrix(node))
        mesh_index = node.get("mesh")
        if isinstance(mesh_index, int) and 0 <= mesh_index < len(meshes):
            for prim in meshes[mesh_index].get("primitives", []):
                position = prim.get("attributes", {}).get("POSITION")
                if not isinstance(position, int) or not 0 <= position < len(accessors):
                    continue
                acc = accessors[position]
                if "min" in acc and "max" in acc:
                    box = (_vec(acc["min"]), _vec(acc["max"]))
                    out += [_apply4(world, corner) for corner in _corners(box)]
        stack += [(child, world) for child in node.get("children", [])]
    return out


_IDENTITY4: Matrix = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def _node_matrix(node: dict) -> Matrix:
    if "matrix" in node:
        m = node["matrix"]  # column-major
        return [[float(m[c * 4 + r]) for c in range(4)] for r in range(4)]
    tx, ty, tz = node.get("translation", (0, 0, 0))
    qx, qy, qz, qw = node.get("rotation", (0, 0, 0, 1))
    sx, sy, sz = node.get("scale", (1, 1, 1))
    r = [
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ]
    return [
        [r[0][0] * sx, r[0][1] * sy, r[0][2] * sz, tx],
        [r[1][0] * sx, r[1][1] * sy, r[1][2] * sz, ty],
        [r[2][0] * sx, r[2][1] * sy, r[2][2] * sz, tz],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _matmul4(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _apply4(m: Matrix, p: Vec) -> Vec:
    x, y, z = (m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))
    return (x, y, z)


# ---------------------------------------------------------------------
# Footprint target
# ---------------------------------------------------------------------


def _outline(node: sexpr.Node) -> tuple[list[tuple[float, float]], str, bool]:
    """`(points, source, smd)`: the F.CrtYd points when the footprint draws a
    courtyard, else its pad corners; `source` says which."""
    pads: list[tuple[float, float]] = []
    courtyard: list[tuple[float, float]] = []
    smd_pads = tht_pads = 0
    attr = ""
    for child in node:
        if not isinstance(child, list):
            continue
        head = sexpr.head(child)
        if head == "attr" and len(child) > 1:
            attr = str(child[1])
        elif head == "pad":
            kind = str(child[2]) if len(child) > 2 else ""
            smd_pads += kind == "smd"
            tht_pads += kind == "thru_hole"
            pads += _pad_corners(child)
        elif head in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
            layer = _find(child, "layer")
            if layer and len(layer) > 1 and str(layer[1]) == "F.CrtYd":
                courtyard += _graphic_points(child)
    smd = attr == "smd" if attr in ("smd", "through_hole") else smd_pads >= tht_pads
    if len(courtyard) >= 2:
        return courtyard, "courtyard", smd
    return pads, "pads", smd


def footprint_target(node: sexpr.Node) -> Target:
    """The box a model should cover, from a parsed `.kicad_mod`."""
    points, _source, smd = _outline(node)
    if not points:
        raise FitError("footprint has no pads or courtyard")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    # Footprint Y points down; the 3D frame's points up.
    return Target(box=(min(xs), -max(ys), max(xs), -min(ys)), smd=smd)


def footprint_size(node: sexpr.Node) -> dict[str, Any] | None:
    """`{w, h, source}` in mm — the courtyard's bounding box, else the pads'
    (`source`: `courtyard` | `pads`). None for a footprint with neither.
    Same points `footprint_target` fits a 3D model to (edaflow SM-019)."""
    points, source, _smd = _outline(node)
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return {
        "w": _round(max(xs) - min(xs)),
        "h": _round(max(ys) - min(ys)),
        "source": source,
    }


def _find(node: list, head: str) -> list | None:
    return next((c for c in node if isinstance(c, list) and sexpr.head(c) == head), None)


def _num(value: Any) -> float:
    return float(str(value))


def _pad_corners(pad: list) -> list[tuple[float, float]]:
    at, size = _find(pad, "at"), _find(pad, "size")
    if not at or not size or len(at) < 3 or len(size) < 3:
        return []
    try:
        x, y = _num(at[1]), _num(at[2])
        angle = math.radians(_num(at[3])) if len(at) > 3 else 0.0
        w, h = _num(size[1]) / 2, _num(size[2]) / 2
    except ValueError:
        return []
    c, s = math.cos(angle), math.sin(angle)
    return [
        (x + dx * c + dy * s, y - dx * s + dy * c)
        for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h))
    ]


def _graphic_points(node: list) -> list[tuple[float, float]]:
    try:
        if sexpr.head(node) == "fp_circle":
            center, end = _find(node, "center"), _find(node, "end")
            if not center or not end:
                return []
            cx, cy = _num(center[1]), _num(center[2])
            r = math.hypot(_num(end[1]) - cx, _num(end[2]) - cy)
            return [(cx - r, cy - r), (cx + r, cy + r)]
        points: list[tuple[float, float]] = []
        for child in node:
            if not isinstance(child, list):
                continue
            head = sexpr.head(child)
            if head in ("start", "end", "mid"):
                points.append((_num(child[1]), _num(child[2])))
            elif head == "pts":
                points += [
                    (_num(p[1]), _num(p[2]))
                    for p in child
                    if isinstance(p, list) and sexpr.head(p) == "xy"
                ]
        return points
    except (ValueError, IndexError):
        return []


# ---------------------------------------------------------------------
# The fit
# ---------------------------------------------------------------------


def place(bounds: Box, placement: Placement) -> Box:
    """`bounds` after KiCad applies `placement` (axis-aligned result)."""
    m = _rotation(placement.rotate)
    sx, sy, sz = placement.scale
    scaled: Box = (
        (bounds[0][0] * sx, bounds[0][1] * sy, bounds[0][2] * sz),
        (bounds[1][0] * sx, bounds[1][1] * sy, bounds[1][2] * sz),
    )
    pts = []
    for corner in _corners(scaled):
        x, y, z = (
            sum(m[i][k] * corner[k] for k in range(3)) + placement.offset[i]
            for i in range(3)
        )
        pts.append((x, y, z))
    lo = (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts))
    hi = (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts))
    return lo, hi


def fit(bounds: Box, target: Target) -> Fit:
    """The best placement of a model with `bounds` onto `target`."""
    if any(hi - lo <= 0 for lo, hi in zip(*bounds, strict=True)):
        raise FitError("model has no extent")
    if _sits_on_board(bounds, target):
        score = _iou(bounds, target.box)
        if score >= _KEEP_AS_PLACED_IOU:
            identity = Placement(offset=(0.0, 0.0, 0.0), rotate=(0.0, 0.0, 0.0))
            return Fit(placement=identity, score=score, recentred=False)
    best: tuple[float, Fit] | None = None
    for index, rotate in enumerate(_ORIENTATIONS):
        rotated = place(bounds, Placement(offset=(0.0, 0.0, 0.0), rotate=rotate))
        for recentred in (False, True):
            if recentred:
                offset = _recentre(rotated, target)
            elif _sits_on_board(rotated, target):
                offset = (0.0, 0.0, 0.0)
            else:
                continue
            score = _iou(_shift(rotated, offset), target.box)
            rank = (
                score
                - _RECENTRE_BIAS * recentred
                - _X_UP_PENALTY * (rotate[1] != 0)
                - _PREFERENCE_STEP * index
            )
            if best is None or rank > best[0]:
                best = (
                    rank,
                    Fit(
                        placement=Placement(offset=offset, rotate=rotate),
                        score=score,
                        recentred=recentred,
                    ),
                )
    if best is None:  # unreachable: the recentred identity always scores
        raise FitError("no candidate placement")
    return best[1]


def _rotation(rotate: Vec) -> Matrix:
    ax, ay, az = (math.radians(-a) for a in rotate)
    rot_x = [[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]]
    rot_y = [[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]]
    rot_z = [[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]]
    return _matmul3(_matmul3(rot_z, rot_y), rot_x)


def _matmul3(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _corners(box: Box) -> list[Vec]:
    (x0, y0, z0), (x1, y1, z1) = box
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def _shift(box: Box, offset: Vec) -> Box:
    (x0, y0, z0), (x1, y1, z1) = box
    dx, dy, dz = offset
    return (x0 + dx, y0 + dy, z0 + dz), (x1 + dx, y1 + dy, z1 + dz)


def _sits_on_board(box: Box, target: Target) -> bool:
    lo_z, hi_z = box[0][2], box[1][2]
    if target.smd:
        return abs(lo_z) <= _SMD_Z_TOLERANCE
    # Through-hole: pins may reach below the board, the body may not.
    return lo_z <= 0 < hi_z and -lo_z <= _THT_MAX_BELOW_FRACTION * (hi_z - lo_z)


def _recentre(box: Box, target: Target) -> Vec:
    x0, y0, x1, y1 = target.box
    dx = (x0 + x1) / 2 - (box[0][0] + box[1][0]) / 2
    dy = (y0 + y1) / 2 - (box[0][1] + box[1][1]) / 2
    keep_z = not target.smd and _sits_on_board(box, target)
    return (dx, dy, 0.0 if keep_z else -box[0][2])


def _iou(box: Box, target: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = box[0][0], box[0][1], box[1][0], box[1][1]
    bx0, by0, bx1, by1 = target
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def _round(value: float) -> float:
    rounded = round(value, 4)
    return 0.0 if rounded == 0 else rounded


def _vec(value: Any) -> Vec:
    x, y, z = (float(v) for v in value)
    return (x, y, z)
