#!/usr/bin/env python3
"""Verify ADR backtick file:line citations still point inside tracked files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADR_GLOB = "docs/adr/*.md"
EXPLICIT_CITE_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+)"
    r":(?P<start>[0-9]+)(?:-(?P<end>[0-9]+))?"
)
SHORTHAND_CITE_RE = re.compile(r"^:(?P<start>[0-9]+)(?:-(?P<end>[0-9]+))?$")
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
ROOT_LEVEL_CITABLE_FILES = {"CLAUDE.md", "CONTRIBUTING.md", "Makefile"}


def _tracked_adr_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "-l", ".", "--", ADR_GLOB],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode not in (0, 1):
        print(result.stderr, file=sys.stderr, end="")
        raise SystemExit(result.returncode)
    return [ROOT / line for line in result.stdout.splitlines()]


def _line_count(path: Path, cache: dict[Path, int]) -> int | None:
    if path in cache:
        return cache[path]
    try:
        cache[path] = len(path.read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        return None
    return cache[path]


def _looks_like_repo_path(path: str) -> bool:
    return "/" in path or path in ROOT_LEVEL_CITABLE_FILES


def main() -> int:
    failures: list[str] = []
    counts: dict[Path, int] = {}

    for adr_path in _tracked_adr_files():
        last_path_on_line: Path | None = None
        for line_no, line in enumerate(
            adr_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            for span in CODE_SPAN_RE.findall(line):
                shorthand = SHORTHAND_CITE_RE.match(span.strip())
                if shorthand and last_path_on_line is not None:
                    start = int(shorthand.group("start"))
                    end = int(shorthand.group("end") or start)
                    target_path = last_path_on_line
                    cites = [(target_path, start, end, span)]
                else:
                    cites = []
                    for match in EXPLICIT_CITE_RE.finditer(span):
                        if not _looks_like_repo_path(match.group("path")):
                            continue
                        target_path = ROOT / match.group("path")
                        start = int(match.group("start"))
                        end = int(match.group("end") or start)
                        last_path_on_line = target_path
                        cites.append((target_path, start, end, match.group(0)))

                for target_path, start, end, raw in cites:
                    target_lines = _line_count(target_path, counts)
                    rel_adr = adr_path.relative_to(ROOT)
                    rel_target = target_path.relative_to(ROOT)
                    if target_lines is None:
                        failures.append(f"{rel_adr}:{line_no}: missing file for `{raw}`")
                    elif start <= 0 or end < start:
                        failures.append(f"{rel_adr}:{line_no}: invalid range in `{raw}`")
                    elif end > target_lines:
                        failures.append(
                            f"{rel_adr}:{line_no}: `{raw}` exceeds {rel_target} "
                            f"line count ({target_lines})"
                        )

    if failures:
        print("Stale ADR file:line citations found:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("ADR file:line citations are within existing tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
