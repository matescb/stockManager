"""The comment header the ngSPICE model library's `bin/export-model` writes.

Two shapes, both read here:

* **NG-010 legacy** (no `Export-Schema:` line): catalogue entry, source
  and converted-file sha256, `Build:`, `Grade (NG-006):`, `Pins (in
  order):`, `Correction …`, `Licence:`.
* **`ngspice-export/1`** (ngSPICE `docs/export-schema.md`): the legacy
  lines kept, plus `Export-Schema:`, `Kind:`, `Build-Token:`,
  `Valid-For-Basis:`, one `Port:` line per port (function, whether it is
  on the LTspice symbol, whether it is internally unconnected, where the
  function came from), `Channels:` and `Match:`.

The schema says a reader must refuse a schema it does not know, so a
header naming another MAJOR version is refused
(`require_supported_schema`, code `eda.spice_export_schema_unsupported`).
A header without `Export-Schema:` is legacy and accepted as before.
Unknown lines are ignored: the exporter may grow fields within a major.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import status

from app.core.errors import ErrorCodes, raise_http

_NG010_MARK = "(ngSPICE model library, NG-010)"
_SCHEMA_NAME = "ngspice-export"
SUPPORTED_MAJOR = 1
_SHA = r"([0-9a-f]{64})"
_MATCH_KEYS = ("part", "manufacturer", "model", "confidence", "level", "key", "manufacturer_match")
_PORT_LINE = re.compile(
    r"Port: (?P<port>\S+) function=(?P<function>\S+) on_symbol=(?P<on_symbol>\S+) "
    r"internally_unconnected=(?P<unconnected>\S+)(?: source=(?P<source>.*))?$"
)
_YES_NO = {"yes": True, "no": False, "true": True, "false": False}


@dataclass(frozen=True)
class PortInfo:
    port: str
    function: str | None
    on_symbol: bool | None
    internally_unconnected: bool
    source: str | None


@dataclass(frozen=True)
class Ng010Header:
    """What an export header says about the file it heads."""

    catalogue_entry: str | None = None
    catalogue_kind: str | None = None
    manufacturer: str | None = None
    source: str | None = None
    source_sha256: str | None = None
    converted: tuple[tuple[str, str], ...] = ()  # (path, sha256)
    build_requirement: str | None = None
    # The build token: stock / patched / patched+runtime / none. From
    # `Build-Token:` (schema 1) or the "needs X;" of the legacy line.
    build_needs: str | None = None
    grade: str | None = None
    grade_limits: tuple[str, ...] = ()
    pins: tuple[str, ...] = ()
    licence: str | None = None
    # "NG009-001 (proposed): Rser 3 -> 3m; APPLIED in this file; …"
    corrections: tuple[str, ...] = ()
    # --- ngspice-export/1 -------------------------------------------
    export_schema: str | None = None  # None = legacy NG-010 header
    kind: str | None = None  # subckt / model_card / native_primitive / none
    valid_for_basis: dict[str, list[str]] = field(default_factory=dict)
    port_table: tuple[PortInfo, ...] = ()
    channels: dict[str, int | None] | None = None  # {package, model}
    match: dict[str, str] = field(default_factory=dict)

    @property
    def internally_unconnected(self) -> list[str]:
        return [p.port for p in self.port_table if p.internally_unconnected]

    def metadata(self) -> dict[str, Any]:
        """The schema-1 fields as JSON, for `part_spice_bindings.export_metadata`."""
        return {
            "export_schema": self.export_schema,
            "kind": self.kind,
            "build": self.build_needs,
            "valid_for_basis": self.valid_for_basis or None,
            "ports": [
                {
                    "port": p.port,
                    "function": p.function,
                    "on_symbol": p.on_symbol,
                    "internally_unconnected": p.internally_unconnected,
                    "source": p.source,
                }
                for p in self.port_table
            ]
            or None,
            "internally_unconnected_ports": self.internally_unconnected or None,
            "channels": self.channels,
            "match": self.match or None,
            "corrections": list(self.corrections) or None,
        }


def _header_lines(text: str) -> list[str]:
    """The leading `*` comment block, `* ` prefix removed."""
    lines: list[str] = []
    for raw in text.splitlines():
        if not raw.startswith("*"):
            break
        lines.append(raw[1:].strip())
    return lines


def _schema_line(lines: list[str]) -> str | None:
    for line in lines:
        if line.startswith("Export-Schema:"):
            return line.split(":", 1)[1].strip()
    return None


def export_schema_problem(text: str) -> str | None:
    """Why the file's export schema cannot be read, or None when it can."""
    schema = _schema_line(_header_lines(text))
    if schema is None:
        return None
    name, _, version = schema.partition("/")
    major = version.split(".", 1)[0]
    if name != _SCHEMA_NAME or not major.isdigit():
        return f"unrecognised Export-Schema {schema[:60]!r}"
    if int(major) != SUPPORTED_MAJOR:
        return (
            f"Export-Schema {schema[:60]!r} is major version {major}; this server reads "
            f"{_SCHEMA_NAME}/{SUPPORTED_MAJOR} and legacy NG-010 headers"
        )
    return None


def require_supported_schema(text: str) -> None:
    """422 `eda.spice_export_schema_unsupported` for an unknown schema."""
    problem = export_schema_problem(text)
    if problem is not None:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCodes.EDA_SPICE_EXPORT_SCHEMA_UNSUPPORTED,
            message=problem,
        )


def _int_or_none(value: str) -> int | None:
    return int(value) if value.isdigit() else None


def _basis(value: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in value.split(";"):
        analysis, sep, tokens = item.strip().partition("=")
        if sep and analysis:
            out[analysis] = [t for t in re.split(r"[,+\s]+", tokens) if t]
    return out


def _match(value: str) -> dict[str, str]:
    # Values may hold spaces ("Analog Devices Inc."), so split only
    # before a known key.
    keys = "|".join(_MATCH_KEYS)
    parts = re.split(rf"\s+(?=(?:{keys})=)", value.strip())
    return dict(p.split("=", 1) for p in parts if "=" in p)


def _port(line: str) -> PortInfo | None:
    m = _PORT_LINE.match(line)
    if m is None:
        return None
    function = m.group("function")
    return PortInfo(
        port=m.group("port"),
        function=None if function == "-" else function,
        on_symbol=_YES_NO.get(m.group("on_symbol")),
        internally_unconnected=_YES_NO.get(m.group("unconnected"), False),
        source=(m.group("source") or None),
    )


def _legacy_line(line: str, values: dict[str, Any]) -> bool:
    """Fold one legacy NG-010 line into `values`; False when not one."""
    if line.startswith("Catalogue entry:"):
        fields = dict(re.findall(r"(\w+)=(\S+)", line))
        values["catalogue_entry"] = fields.get("name")
        values["catalogue_kind"] = fields.get("kind")
    elif line.startswith("Manufacturer:"):
        maker = line.split(":", 1)[1].split(";", 1)[0].strip()
        values["manufacturer"] = None if maker == "not established" else maker
    elif m := re.match(rf"Source: (\S+) sha256 {_SHA}", line):
        values["source"], values["source_sha256"] = m.group(1), m.group(2)
    elif m := re.match(r"Source: (\S+)", line):
        values["source"] = m.group(1)
    elif m := re.match(rf"Converted file: (\S+) sha256 {_SHA}", line):
        values["converted"].append((m.group(1), m.group(2)))
    elif line.startswith("Build:"):
        build = line.split(":", 1)[1].strip()
        values["build_parts"].append(build)
        if m := re.match(r"needs (\S+?);", build):
            values.setdefault("build_needs", m.group(1))
    elif line.startswith("Correction "):
        values["corrections"].append(line[len("Correction "):].strip())
    elif line.startswith("Grade (NG-006):"):
        values["grade"] = line.split(":", 1)[1].strip()
    elif line.startswith("limit:"):
        values["limits"].append(line.split(":", 1)[1].strip())
    elif line.startswith("Pins (in order):"):
        pins = line.split(":", 1)[1].strip()
        if pins and not pins.startswith("see "):
            values["pins"] = tuple(pins.split())
    elif line.startswith("Licence:"):
        values["licence"] = line.split(":", 1)[1].strip()
    else:
        return False
    return True


def _schema1_line(line: str, values: dict[str, Any]) -> None:
    key, _, value = line.partition(":")
    value = value.strip()
    if key == "Export-Schema":
        values["export_schema"] = value
    elif key == "Kind":
        values["kind"] = value
    elif key == "Build-Token":
        values["build_needs"] = value  # authoritative over the legacy parse
    elif key == "Valid-For-Basis":
        values["valid_for_basis"] = {} if value == "-" else _basis(value)
    elif key == "Port" and (port := _port(line)) is not None:
        values["ports"].append(port)
    elif key == "Channels":
        fields = dict(re.findall(r"(\w+)=(\S+)", value))
        values["channels"] = {
            "package": _int_or_none(fields.get("package", "-")),
            "model": _int_or_none(fields.get("model", "-")),
        }
    elif key == "Match":
        values["match"] = _match(value)


def parse_ng010_header(text: str) -> Ng010Header | None:
    """The export header, or None when the file was not exported by it.

    Recognised by the NG-010 marker on its first line or an
    `Export-Schema:` line, so an ordinary vendor file whose comments
    mention "Pins" is never mistaken for one. Does NOT check the schema
    version — `require_supported_schema` does, at the upload lane.
    """
    lines = _header_lines(text)
    if not lines or (_NG010_MARK not in lines[0] and _schema_line(lines) is None):
        return None
    values: dict[str, Any] = {
        "converted": [], "build_parts": [], "corrections": [], "limits": [], "ports": [],
    }
    for line in lines[1:]:
        if not _legacy_line(line, values):
            _schema1_line(line, values)
    return Ng010Header(
        catalogue_entry=values.get("catalogue_entry"),
        catalogue_kind=values.get("catalogue_kind"),
        manufacturer=values.get("manufacturer"),
        source=values.get("source"),
        source_sha256=values.get("source_sha256"),
        converted=tuple(values["converted"]),
        build_requirement="; ".join(values["build_parts"]) or None,
        build_needs=values.get("build_needs"),
        corrections=tuple(values["corrections"]),
        grade=values.get("grade"),
        grade_limits=tuple(values["limits"]),
        pins=values.get("pins") or (),
        licence=values.get("licence"),
        export_schema=values.get("export_schema"),
        kind=values.get("kind"),
        valid_for_basis=values.get("valid_for_basis") or {},
        port_table=tuple(values["ports"]),
        channels=values.get("channels"),
        match=values.get("match") or {},
    )


def model_source_text(header: Ng010Header) -> str:
    """One line naming where an exported model came from, for storage."""
    parts = []
    if header.catalogue_entry:
        parts.append(f"ngspice-library:{header.catalogue_entry}")
    if header.source:
        parts.append(
            header.source
            + (f" sha256 {header.source_sha256}" if header.source_sha256 else "")
        )
    if header.match.get("confidence"):
        parts.append(f"match {header.match['confidence']}")
    parts += [f"correction {c}" for c in header.corrections]
    return "; ".join(parts)
