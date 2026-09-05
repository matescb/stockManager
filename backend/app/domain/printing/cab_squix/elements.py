"""Composable JScript element model for cab SQUIX printers.

Each element class emits one JScript line via :meth:`to_jscript`. A
:class:`Job` collects elements together with the job header
(``m m`` / ``J`` / ``H`` / ``S``) and trailing ``A n`` print-count line.

Reference: cab JScript Programming Manual, Edition 05/2025.

PROVENANCE: vendored unmodified from the MIT-licensed cab_squix toolkit at
/mnt/data/WORK/cab. This copy was re-vendored from the sibling skladVA project
(/mnt/data/WORK/sklad, ``backend/app/printing/cab_squix/``), which vendored it
first; both projects drive the same physical cab SQUIX printer. See
``app/domain/printing/cab_squix/__init__.py`` for details.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# --------------------------------------------------------------------- elements


@dataclass(frozen=True)
class Element:
    """Base class. Subclasses must override :meth:`to_jscript`."""

    def to_jscript(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class Text(Element):
    """``T`` command (manual section 4.16, page 223).

    Wire syntax: ``T[:name;]x,y,r,font,size[,effects];text``

    Common fonts: 3 = Swiss 721, 5 = Swiss 721 Bold, 596 = Monospace 821,
    7 = CG Triumvirate Condensed Bold. Size is in points (``ptNN``).
    """
    x: float
    y: float
    text: str
    # A built-in device font id (int, e.g. 3/5/596) OR the name of a TrueType
    # font loaded onto the printer (str, e.g. "skautbold") — the CAB ``T``
    # command accepts either in the font position.
    font: int | str = 3
    size_pt: int = 10
    rotation: int = 0

    def to_jscript(self) -> str:
        return f"T {self.x:g},{self.y:g},{self.rotation},{self.font},pt{self.size_pt};{self.text}"


@dataclass(frozen=True)
class Barcode1D(Element):
    """1D linear barcode (``B`` command, ratio-aware variant).

    Wire syntax: ``B[:name;]x,y,r,type[+options],height,ne[,ratio][,fx];text``

    Examples of ``type``: ``CODE128``, ``CODE39``, ``CODABAR``, ``EAN13``,
    ``UCC128``, ``MSI``. Lowercase variants suppress the human-readable line.

    ``ratio`` is only meaningful for ratio-oriented symbologies (Code 39,
    Codabar, 2-of-5 Interleaved, Plessey, MSI). Leave as ``None`` for
    Code 128, EAN/UPC and the rest.
    """
    x: float
    y: float
    text: str
    type: str = "CODE128"
    height_mm: float = 10.0
    ne_mm: float = 0.40
    ratio: float | None = None
    rotation: int = 0
    options: str = ""  # e.g. "+MOD10", "+BARS", "+WS5"

    def to_jscript(self) -> str:
        params = f"{self.height_mm:g},{self.ne_mm:g}"
        if self.ratio is not None:
            params += f",{self.ratio:g}"
        return (
            f"B {self.x:g},{self.y:g},{self.rotation},"
            f"{self.type}{self.options},{params};{self.text}"
        )


@dataclass(frozen=True)
class Barcode2D(Element):
    """2D / matrix barcode (``B`` command, dotsize variant).

    Wire syntax for QR / DataMatrix / Aztec / rMQR::

        B[:name;]x,y,r,type[+options],dotsize[,fx];text

    Wire syntax for PDF 417 (uses ``height,ne,ratio`` instead of dotsize)::

        B[:name;]x,y,r,PDF417[+options],height,ne,ratio[,fx];text
    """
    x: float
    y: float
    text: str
    type: str = "QRCODE"           # QRCODE | DATAMATRIX | AZTEC | PDF417 | RMQR
    dotsize_mm: float = 0.5
    rotation: int = 0
    options: str = ""              # e.g. "+ELM+MODEL2", "+RECT", "+ROWS20+COLS20"
    # PDF417-only knobs (ignored otherwise):
    pdf417_height_mm: float | None = None  # height per row, mm
    pdf417_ne_mm: float | None = None      # narrow element width, mm
    pdf417_ratio: float | None = None      # cell-to-row ratio

    def to_jscript(self) -> str:
        if self.type.upper() == "PDF417":
            h = self.pdf417_height_mm if self.pdf417_height_mm is not None else 0.1
            n = self.pdf417_ne_mm if self.pdf417_ne_mm is not None else 0.38
            r = self.pdf417_ratio if self.pdf417_ratio is not None else 1
            return (
                f"B {self.x:g},{self.y:g},{self.rotation},"
                f"{self.type}{self.options},{h:g},{n:g},{r:g};{self.text}"
            )
        return (
            f"B {self.x:g},{self.y:g},{self.rotation},"
            f"{self.type}{self.options},{self.dotsize_mm:g};{self.text}"
        )


# --------------------------------------------------------------------- job


@dataclass
class Job:
    """A full JScript print job.

    Header lines (set on every job):
        ``m m``                              -- units = mm (manual section 3.9)
        ``J``                                -- start of job (section 4.10)
        ``H <heat>,<speed>,<method>``        -- print parameters (section 4.8)
        ``S <ptype>;<xo>,<yo>,<ho>,<dy>,<wd>`` -- label geometry (section 4.15)

    Footer line: ``A <copies>`` (section 4.1).

    Element body lines come from :meth:`to_jscript` on each registered Element.
    """
    width_mm: float
    height_mm: float
    gap_mm: float = 3.0
    ptype: str = "l1"           # l1 = die-cut + gap, e = endless, l0/l2 = reflective
    heat: int = 100
    speed: int = 0              # 0 = printer default
    method: str = "T"           # T = thermal transfer, D = thermal direct
    copies: int = 1
    xo: float = 0
    yo: float = 0
    elements: list[Element] = field(default_factory=list)

    def add(self, *items: Element) -> "Job":
        """Append one or more elements. Returns self for chaining."""
        self.elements.extend(items)
        return self

    def extend(self, items: Iterable[Element]) -> "Job":
        """Append an iterable of elements. Returns self for chaining."""
        self.elements.extend(items)
        return self

    @property
    def pitch_mm(self) -> float:
        """Label-to-label pitch (``dy`` in the S command).

        Equals ``height_mm + gap_mm`` for die-cut material; equals
        ``height_mm`` for endless / continuous material.
        """
        return self.height_mm + (self.gap_mm if self.ptype == "l1" else 0.0)

    def to_jscript(self) -> str:
        """Render the full JScript job, CRLF-terminated."""
        lines = [
            "m m",
            "J",
            f"H {self.heat},{self.speed},{self.method}",
            f"S {self.ptype};{self.xo:g},{self.yo:g},"
            f"{self.height_mm:g},{self.pitch_mm:g},{self.width_mm:g}",
        ]
        lines.extend(el.to_jscript() for el in self.elements)
        lines.append(f"A {self.copies}")
        return "\r\n".join(lines) + "\r\n"
