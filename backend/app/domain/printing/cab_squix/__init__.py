"""Vendored cab SQUIX printer driver (JScript over raw TCP).

PROVENANCE
----------
Vendored from the standalone ``cab_squix`` toolkit at /mnt/data/WORK/cab
(MIT licensed). This copy was re-vendored from the sibling **skladVA** project
(/mnt/data/WORK/sklad, ``backend/app/printing/cab_squix/``), which vendored it
first and drives the *same* physical cab SQUIX industrial label printer. Only
the transport + element model are vendored here (``printer.py``,
``elements.py``); the CLI / ``__main__`` / demo templates are intentionally NOT
copied — stockManager renders its own labels through
:mod:`app.domain.printing.print_service` rather than the demo set.

Upstream reference: cab JScript Programming Manual, Edition 05/2025
(firmware 5.46.3). The driver is unmodified apart from this provenance note so
upstream fixes (and skladVA's) can be re-vendored cleanly.

License: MIT (see the upstream project). Copyright the cab_squix authors.

Public surface:
    CabPrinter, Status, PrinterError, DEFAULT_HOST, DEFAULT_PORT
    Element, Text, Barcode1D, Barcode2D, Job
"""

from .elements import Barcode1D, Barcode2D, Element, Job, Text
from .printer import DEFAULT_HOST, DEFAULT_PORT, CabPrinter, PrinterError, Status

__all__ = [
    "CabPrinter",
    "PrinterError",
    "Status",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "Element",
    "Text",
    "Barcode1D",
    "Barcode2D",
    "Job",
]
