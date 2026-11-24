"""Read the port list out of a SPICE model file (SM-009).

Pure text in, dataclasses out — no database, no storage. Two readers:

* `parse_models` finds every TOP-LEVEL `.subckt` and `.model` in a file.
  A `.subckt` gives its name and ordered port list; a `.model` gives its
  name and device type, and — for the device types KiCad's built-in
  models know — the pin names KiCad uses for them, in SPICE node order.
* The comment header the ngSPICE model library's `bin/export-model`
  writes is read by `spice_header.py`.

What is NOT attempted: evaluating parameters, expanding `.include` /
`.lib` sections, or checking that the netlist inside a `.subckt` makes
sense. The simulator is the arbiter of the body; this module only reads
the interface line, which is the part a wrong `Sim.Pins` gets wrong.

Grammar handled (ngspice / LTspice / PSpice dialects agree on this much):

* `+` at the start of a line continues the previous logical line.
* `*` at the start of a line is a comment; `;` and `$` (after
  whitespace) start an inline comment. Blank lines are skipped.
* Dot-directives are case-insensitive; names keep their spelling.
* `.subckt NAME p1 p2 … [PARAMS: k=v …]` — the port list ends at
  `PARAMS:` (or `PARAMS` / `OPTIONAL:` / `TEXT:`) or at the first token
  holding `=`, which is how LTspice writes defaults without `PARAMS:`.
* Nested `.subckt … .ends` blocks are skipped: only top-level definitions
  are things a symbol can bind to.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Device type of a `.model` card → the pin names KiCad's built-in model of
# that type uses, in SPICE node order. These are the names that appear
# on the right of `Sim.Pins` (KiCad's own Simulation_SPICE library writes
# "1=K 2=A" for a diode, "1=C 2=B 3=E" for a BJT, "1=D 2=G 3=S" for a
# MOSFET). Types not listed — resistors, switches, behavioural cards,
# 4-terminal MOSFETs with an explicit bulk — are refused as not
# checkable rather than guessed at.
PRIMITIVE_PORTS: dict[str, tuple[str, ...]] = {
    "D": ("A", "K"),
    "NPN": ("C", "B", "E"),
    "PNP": ("C", "B", "E"),
    "NJF": ("D", "G", "S"),
    "PJF": ("D", "G", "S"),
    "NMOS": ("D", "G", "S"),
    "PMOS": ("D", "G", "S"),
    "VDMOS": ("D", "G", "S"),
}

# KiCad's built-in passive/diode models, for a part simulated as a
# primitive with parameters and no model file (`model_kind
# native_primitive`). KiCad's Simulation_SPICE library writes "1=+ 2=-"
# for R, C and L.
NATIVE_PORTS: dict[str, tuple[str, ...]] = {
    "R": ("+", "-"),
    "C": ("+", "-"),
    "L": ("+", "-"),
    "D": ("A", "K"),
}

_PORT_TERMINATORS = frozenset({"params:", "params", "optional:", "text:"})
_INLINE_COMMENT = re.compile(r"(?:^|\s)[;$].*$")


@dataclass(frozen=True)
class SpiceModel:
    """One top-level definition a symbol can be bound to."""

    name: str
    kind: str  # "subckt" | "model"
    ports: tuple[str, ...]  # empty for a `.model` of an uncheckable type
    device_type: str | None = None  # `.model` only, upper-cased
    params: tuple[str, ...] = ()  # `.subckt` PARAMS: names, in order

    @property
    def checkable(self) -> bool:
        return bool(self.ports)


def logical_lines(text: str) -> list[str]:
    """Comment-stripped lines with `+` continuations joined."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        line = _INLINE_COMMENT.sub("", line).strip()
        if not line:
            continue
        if line.startswith("+"):
            if out:
                out[-1] = f"{out[-1]} {line[1:].strip()}"
            continue
        out.append(line)
    return out


def _tokens(line: str) -> list[str]:
    # `PARAMS:` glued to its first value (`PARAMS:R=1`) is legal PSpice.
    spaced = re.sub(r"(?i)\bparams:", " PARAMS: ", line)
    return spaced.replace("(", " ( ").replace(")", " ) ").split()


def _subckt(tokens: list[str]) -> SpiceModel | None:
    if len(tokens) < 2:
        return None
    name, ports, params = tokens[1], [], []
    in_params = False
    for tok in tokens[2:]:
        low = tok.lower()
        if low in _PORT_TERMINATORS:
            in_params = True
            continue
        if in_params or "=" in tok:
            in_params = True
            key = tok.split("=", 1)[0]
            if key:
                params.append(key)
            continue
        ports.append(tok)
    return SpiceModel(name=name, kind="subckt", ports=tuple(ports), params=tuple(params))


def _model(line: str) -> SpiceModel | None:
    # `.model NAME TYPE(…)`, `.model NAME TYPE (…)` and `.model NAME TYPE k=v`
    match = re.match(r"(?i)\.model\s+(\S+)\s+([A-Za-z_]+)", line)
    if match is None:
        return None
    name, dtype = match.group(1), match.group(2).upper()
    return SpiceModel(
        name=name,
        kind="model",
        ports=PRIMITIVE_PORTS.get(dtype, ()),
        device_type=dtype,
    )


def parse_models(text: str) -> list[SpiceModel]:
    """Every top-level `.subckt` / `.model` in `text`, in file order."""
    found: list[SpiceModel] = []
    depth = 0
    for line in logical_lines(text):
        low = line.lower()
        if low.startswith(".subckt"):
            if depth == 0:
                parsed = _subckt(_tokens(line))
                if parsed is not None:
                    found.append(parsed)
            depth += 1
        elif low.startswith(".ends"):
            depth = max(0, depth - 1)
        elif low.startswith(".model") and depth == 0:
            parsed = _model(line)
            if parsed is not None:
                found.append(parsed)
    return found


def find_model(models: list[SpiceModel], name: str) -> SpiceModel | None:
    """Case-insensitive lookup — SPICE names are case-insensitive."""
    wanted = name.lower()
    return next((m for m in models if m.name.lower() == wanted), None)


# ---------------------------------------------------------------------
# KiCad `Sim.Pins`
# ---------------------------------------------------------------------


def parse_sim_pins(value: str | None) -> list[tuple[str, str]] | None:
    """`"1=IN+ 2=IN-"` → `[("1", "IN+"), ("2", "IN-")]`.

    None when the string is empty or not in KiCad 7+'s
    `symbolPin=modelPin` form (a KiCad 6 `Spice_Node_Sequence` like
    "1 2 3" is not, and cannot be checked by name).
    """
    if not value or not value.strip():
        return None
    pairs: list[tuple[str, str]] = []
    for tok in value.split():
        pin, sep, port = tok.partition("=")
        if not sep or not pin or not port:
            return None
        pairs.append((pin, port))
    return pairs


def format_sim_pins(pairs: list[tuple[str, str]]) -> str:
    """The inverse of `parse_sim_pins`."""
    return " ".join(f"{pin}={port}" for pin, port in pairs)
