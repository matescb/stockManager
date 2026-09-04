"""Regenerate `cube.step` — the STEP fixture for the 3D-preview tests.

Not part of the app or its dependencies: run it in a throwaway virtualenv
that has build123d installed (see PROVENANCE.md). It emits a 10 mm cube as
AP214 STEP and normalises the embedded timestamp so the output is
byte-reproducible.

    python -m venv /tmp/cadgen
    /tmp/cadgen/bin/pip install build123d==0.11.1
    /tmp/cadgen/bin/python gen_cube.py
"""
from __future__ import annotations

import hashlib
import os
import re

from build123d import Box, export_step

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUT = os.path.join(_HERE, "cube.step")


def main() -> None:
    export_step(Box(10, 10, 10), _OUT)
    with open(_OUT, encoding="utf-8") as handle:
        text = handle.read()
    # Drop the wall-clock timestamp OCC stamps into FILE_NAME so the
    # fixture doesn't churn on every regeneration.
    text = re.sub(
        r"'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'",
        "'1970-01-01T00:00:00'",
        text,
        count=1,
    )
    with open(_OUT, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"wrote {_OUT} ({len(text)} bytes)")
    print("sha256", hashlib.sha256(text.encode("utf-8")).hexdigest())


if __name__ == "__main__":
    main()
