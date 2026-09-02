"""Fetching one LCSC part from EasyEDA and converting it to KiCad.

The whole EasyEDA dialect — its API shape, its canvas geometry, its OBJ
→ WRL conversion — lives in the `easyeda2kicad` package, used here as a
library and never as a CLI. This module is the seam: it drives that
package, hands back the same `ImportPlan` a vendor zip produces, and is
the single place a test monkeypatches to keep the network out of the
suite.

Two things are ours to enforce rather than the library's:

* **A wall-clock budget.** `easyeda2kicad` uses `urllib` with a 30 s
  per-request timeout and makes up to three requests, so on its own it
  can hold a worker for a minute and a half. `fetch_plan` checks the
  deadline between stages and gives up on the optional ones, which keeps
  the caller's own timeout from having to fire.
* **Partial success.** A component with no 3D model, or one whose
  footprint fails to convert, still yields its symbol. The stages are
  independent and a failure in one is a note in `skipped`.

Everything here blocks; call it through `run_in_threadpool`.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

from app.domain.eda import sexpr, storage
from app.domain.eda.vendor_zip import ImportPlan, PendingDatafile, PendingEntry, Skipped

__all__ = [
    "FETCH_BUDGET_SECONDS",
    "HARD_TIMEOUT_SECONDS",
    "SOURCE",
    "LcscError",
    "LcscNotFound",
    "LcscUnavailable",
    "fetch_plan",
]

# Total wall-clock budget for one fetch, re-checked between EVERY stage.
#
# `easyeda2kicad` hard-codes `timeout=30` on each of its up-to-three
# `urllib` calls and exposes no way to lower it, so one already-in-flight
# call can still overrun this budget. What the checks buy is that we
# never START another one past the deadline — the worst case is a single
# overrunning call rather than three back to back (P3 security review).
# The CLIENT never waits on that either way: `eda_import._fetch_lcsc_plan`
# wraps this in `asyncio.wait_for`.
FETCH_BUDGET_SECONDS = 20.0

# The outer wait the route puts around the whole worker call. It MUST sit
# above `FETCH_BUDGET_SECONDS`: when the two were equal the outer fired
# first every time, so the per-stage checks — including the branch that
# skips the 3D download — could never be reached (P3 code review MED).
HARD_TIMEOUT_SECONDS = FETCH_BUDGET_SECONDS + 10.0

SOURCE = "easyeda"

# The library nickname written into the converted symbol's `Footprint`
# property. The part's real footprint comes from `part_eda`, so this is
# only what a user sees if they open the raw symbol file.
_FOOTPRINT_LIB_NICK = "stockmgr"

_SKIP_NO_SYMBOL = "EasyEDA returned no schematic symbol for this part"
_SKIP_NO_FOOTPRINT = "EasyEDA returned no PCB footprint for this part"
_SKIP_NO_3D = "EasyEDA has no 3D model for this part"
_SKIP_BUDGET = "skipped — the fetch ran out of time"
_SKIP_3D_TOO_LARGE = "3D model exceeds the size limit for its type"
_SKIP_3D_ESCAPED = "3D model was written outside the conversion directory"

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class LcscError(Exception):
    """Base for the two failures the route maps onto HTTP statuses."""


class LcscNotFound(LcscError):
    """EasyEDA has no CAD data under this LCSC id."""


class LcscUnavailable(LcscError):
    """EasyEDA could not be reached, or answered with something unusable."""


def _in_budget(deadline: float) -> bool:
    return time.monotonic() < deadline


def fetch_plan(lcsc_id: str) -> ImportPlan:
    """Fetch and convert `lcsc_id`, blocking, into an importable plan.

    Raises `LcscNotFound` / `LcscUnavailable`; every other failure mode
    lands in the plan's `skipped` notes.
    """
    # Imported lazily: `easyeda2kicad` pulls in its whole converter tree,
    # and only this one endpoint needs it.
    from easyeda2kicad.easyeda.easyeda_api import EasyedaApi

    deadline = time.monotonic() + FETCH_BUDGET_SECONDS
    api = EasyedaApi()  # use_cache=False — nothing is written to disk.

    try:
        cad_data = api.get_cad_data_of_component(lcsc_id=lcsc_id)
    except Exception as exc:  # noqa: BLE001 — urllib/JSON/attribute, all "upstream broke"
        raise LcscUnavailable(str(exc)) from exc
    if not cad_data:
        # `easyeda2kicad` swallows a URLError into the same empty dict it
        # returns for an unknown part, so this can't be told apart from a
        # network failure. "Not found" is the far commoner cause and the
        # message names the other possibility.
        raise LcscNotFound(lcsc_id)
    if not _in_budget(deadline):
        raise LcscUnavailable(
            f"EasyEDA took longer than {FETCH_BUDGET_SECONDS}s for {lcsc_id}"
        )

    skipped: list[Skipped] = []
    symbols: list[PendingEntry] = []
    footprints: list[PendingEntry] = []
    datafiles: list[PendingDatafile] = []

    symbol_entry = _convert_symbol(cad_data, skipped=skipped)
    if symbol_entry is not None:
        symbols.append(symbol_entry)

    with tempfile.TemporaryDirectory(prefix="stockmgr-lcsc-") as tmp:
        if _in_budget(deadline):
            footprint_entry = _convert_footprint(cad_data, tmp=Path(tmp), skipped=skipped)
            if footprint_entry is not None:
                footprints.append(footprint_entry)
        else:
            skipped.append(Skipped(filename="footprint", reason=_SKIP_BUDGET))

        if _in_budget(deadline):
            datafiles.extend(
                _convert_3d(
                    cad_data, api=api, tmp=Path(tmp), deadline=deadline, skipped=skipped
                )
            )
        else:
            skipped.append(Skipped(filename="3d", reason=_SKIP_BUDGET))

    if not symbols and not footprints and not datafiles:
        raise LcscUnavailable(f"nothing convertible in EasyEDA's data for {lcsc_id}")

    return ImportPlan(
        vendor=SOURCE,
        symbols=tuple(symbols),
        footprints=tuple(footprints),
        datafiles=tuple(datafiles),
        skipped=tuple(skipped),
    )


def _convert_symbol(cad_data: dict, *, skipped: list[Skipped]) -> PendingEntry | None:
    from easyeda2kicad.easyeda.easyeda_importer import EasyedaSymbolImporter
    from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad

    try:
        symbol = EasyedaSymbolImporter(easyeda_cp_cad_data=cad_data).get_symbol()
        text = ExporterSymbolKicad(symbol=symbol, lib_path=None).export(
            footprint_lib_name=_FOOTPRINT_LIB_NICK
        )
        # `export` yields a bare `(symbol …)` — the same shape the P2
        # single-file upload accepts — so it parses with `entries`.
        found = sexpr.entries(text)
    except Exception:  # noqa: BLE001 — the converter raises whatever the data broke on
        skipped.append(Skipped(filename="symbol", reason=_SKIP_NO_SYMBOL))
        return None
    if not found or not found[0][0]:
        skipped.append(Skipped(filename="symbol", reason=_SKIP_NO_SYMBOL))
        return None
    name, node = found[0]
    return PendingEntry(name=name, node=node, filename=f"{_safe(name)}.kicad_sym")


def _convert_footprint(
    cad_data: dict, *, tmp: Path, skipped: list[Skipped]
) -> PendingEntry | None:
    from easyeda2kicad.easyeda.easyeda_importer import EasyedaFootprintImporter
    from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad

    try:
        footprint = EasyedaFootprintImporter(easyeda_cp_cad_data=cad_data).get_footprint()
        path = tmp / f"{_safe(footprint.info.name)}.kicad_mod"
        # The exporter only writes to a file; the model path it embeds is
        # rewritten again by `importer._rewrite_models` once the 3D rows
        # exist, so what matters here is only that the filename stem
        # matches the model files written alongside it.
        ExporterFootprintKicad(footprint=footprint).export(
            footprint_full_path=str(path),
            model_3d_path=".",
            model_3d_extension="step",
        )
        node = sexpr.parse(path.read_text(encoding="utf-8"))
        name = sexpr.entry_name(node)
    except Exception:  # noqa: BLE001 — same reasoning as the symbol stage
        skipped.append(Skipped(filename="footprint", reason=_SKIP_NO_FOOTPRINT))
        return None
    if not name:
        skipped.append(Skipped(filename="footprint", reason=_SKIP_NO_FOOTPRINT))
        return None
    return PendingEntry(name=name, node=node, filename=path.name)


def _convert_3d(
    cad_data: dict, *, api, tmp: Path, deadline: float, skipped: list[Skipped]
) -> list[PendingDatafile]:
    from easyeda2kicad.easyeda.easyeda_importer import Easyeda3dModelImporter
    from easyeda2kicad.kicad.export_kicad_3d_model import Exporter3dModelKicad

    out_dir = tmp / "models"
    try:
        model = Easyeda3dModelImporter(
            easyeda_cp_cad_data=cad_data, download_raw_3d_model=True, api=api
        ).output
        if model is None:
            skipped.append(Skipped(filename="3d", reason=_SKIP_NO_3D))
            return []
        if not _in_budget(deadline):
            skipped.append(Skipped(filename="3d", reason=_SKIP_BUDGET))
            return []
        # The exporter builds its output path as `output_dir/{name}.wrl`
        # straight from EasyEDA's JSON `title`, so an upstream name
        # carrying `../` writes OUTSIDE the temp directory — a remote
        # arbitrary-file-write (P3 security review HIGH-2). Sanitise the
        # name on BOTH objects before anything is written: the importer's
        # model, and the converted output the exporter actually reads.
        _rename_model(model)
        exporter = Exporter3dModelKicad(model_3d=model)
        # `output` is None when there was no raw geometry to convert.
        _rename_model(getattr(exporter, "output", None))
        if not exporter.export(output_dir=str(out_dir), overwrite=True):
            skipped.append(Skipped(filename="3d", reason=_SKIP_NO_3D))
            return []
    except Exception:  # noqa: BLE001 — same reasoning as the symbol stage
        skipped.append(Skipped(filename="3d", reason=_SKIP_NO_3D))
        return []

    out: list[PendingDatafile] = []
    # STEP first so it lands at position 0 on the footprint.
    for suffix, kind in ((".step", "step"), (".wrl", "wrl")):
        for path in sorted(out_dir.glob(f"*{suffix}")):
            found = _read_converted(path, kind=kind, out_dir=out_dir, skipped=skipped)
            if found is not None:
                out.append(found)
    if not out and not skipped:
        skipped.append(Skipped(filename="3d", reason=_SKIP_NO_3D))
    return out


def _rename_model(model) -> None:
    """Force a model object's `name` through the filename sanitiser.

    Best-effort: the attribute is whatever `easyeda2kicad` version is
    installed, so a missing or read-only `name` must not break the fetch.
    """
    if model is None:
        return
    try:
        current = getattr(model, "name", None)
        if isinstance(current, str):
            model.name = _safe(current)
    except (AttributeError, TypeError):
        return


def _read_converted(
    path: Path, *, kind: str, out_dir: Path, skipped: list[Skipped]
) -> PendingDatafile | None:
    """Read one converted 3D file, bounded and confined.

    Belt-and-braces after the name sanitiser: resolve the path and refuse
    anything that doesn't land under `out_dir`, which also covers a
    symlink planted inside it. And cap the read — `validated_datafile`
    only checks the leading magic, so without this an upstream model of
    any size is loaded whole (P3 security review MED).
    """
    root = os.path.realpath(out_dir)
    real = os.path.realpath(path)
    if not real.startswith(root + os.sep):
        skipped.append(Skipped(filename=_safe(path.name), reason=_SKIP_3D_ESCAPED))
        return None
    cap = storage.max_bytes_for(kind)
    try:
        if os.path.getsize(real) > cap:
            skipped.append(Skipped(filename=_safe(path.name), reason=_SKIP_3D_TOO_LARGE))
            return None
        data = path.read_bytes()
    except OSError:
        skipped.append(Skipped(filename=_safe(path.name), reason=_SKIP_NO_3D))
        return None
    if len(data) > cap:
        skipped.append(Skipped(filename=_safe(path.name), reason=_SKIP_3D_TOO_LARGE))
        return None
    return PendingDatafile(kind=kind, name=_safe(path.name), data=data)


def _safe(name: str) -> str:
    """A filename-safe rendering of an upstream name.

    Only ever used for a row/display name and a temp-directory file —
    stored blobs are content-addressed — but EasyEDA names carry slashes
    and non-ASCII, and neither belongs in either place.
    """
    cleaned = _SAFE_NAME_RE.sub("_", name).strip("._-")
    return cleaned[:200] or "easyeda"
