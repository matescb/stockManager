#!/usr/bin/env python3
"""Fail when Docker base-image digest pins are stale or undocumented.

The Dockerfiles intentionally pin base images by digest. Dependabot rotates
those digests, and the nearby "Digest pinned on YYYY-MM-DD" comment records
when the pinned digest entered the repo. This guard keeps manual review queues
from leaving a known-old base layer in place indefinitely.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


DEFAULT_DOCKERFILES = (
    Path("backend/Dockerfile"),
    Path("web/Dockerfile.prod"),
)

PINNED_ON_RE = re.compile(r"^\s*#\s*Digest pinned on (\d{4}-\d{2}-\d{2})\b")
FROM_RE = re.compile(r"^\s*FROM\s+(?P<image>\S+)(?:\s+AS\s+\S+)?\s*$", re.IGNORECASE)
SHA256_RE = re.compile(r"@sha256:[0-9a-f]{64}\b", re.IGNORECASE)


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def _parse_iso_date(raw: str, *, path: Path, line: int) -> date | Violation:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return Violation(path, line, f"invalid digest pin date {raw!r}")


def check_file(path: Path, *, today: date, max_age_days: int) -> list[Violation]:
    violations: list[Violation] = []
    pending_pin: tuple[date, int] | None = None
    saw_from = False

    if not path.exists():
        return [Violation(path, 1, "Dockerfile does not exist")]

    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        pinned_on = PINNED_ON_RE.match(line)
        if pinned_on:
            parsed = _parse_iso_date(pinned_on.group(1), path=path, line=line_no)
            if isinstance(parsed, Violation):
                violations.append(parsed)
                pending_pin = None
            else:
                pending_pin = (parsed, line_no)
            continue

        from_match = FROM_RE.match(line)
        if not from_match:
            continue

        saw_from = True
        image = from_match.group("image")
        if not SHA256_RE.search(image):
            violations.append(
                Violation(path, line_no, f"base image is not digest-pinned: {image}")
            )
            pending_pin = None
            continue

        if pending_pin is None:
            violations.append(
                Violation(
                    path,
                    line_no,
                    "digest-pinned FROM is missing preceding "
                    "'Digest pinned on YYYY-MM-DD' comment",
                )
            )
            continue

        pinned_date, comment_line = pending_pin
        age_days = (today - pinned_date).days
        if age_days < 0:
            violations.append(
                Violation(
                    path,
                    comment_line,
                    f"digest pin date {pinned_date.isoformat()} is in the future",
                )
            )
        elif age_days > max_age_days:
            violations.append(
                Violation(
                    path,
                    comment_line,
                    f"digest pin is {age_days} days old; max is {max_age_days} days",
                )
            )
        pending_pin = None

    if not saw_from:
        violations.append(Violation(path, 1, "no FROM instruction found"))

    return violations


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check Dockerfile base-image digest pin freshness."
    )
    parser.add_argument(
        "dockerfiles",
        nargs="*",
        type=Path,
        default=list(DEFAULT_DOCKERFILES),
        help="Dockerfiles to check (default: backend and web production images).",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=30,
        help="Maximum allowed age for digest pin comments.",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=date.today(),
        help="Override today's date for tests, in YYYY-MM-DD format.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.max_age_days < 0:
        print("--max-age-days must be non-negative", file=sys.stderr)
        return 2

    violations: list[Violation] = []
    for dockerfile in args.dockerfiles:
        violations.extend(
            check_file(dockerfile, today=args.today, max_age_days=args.max_age_days)
        )

    if violations:
        print("Docker base-image digest freshness check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.format()}", file=sys.stderr)
        return 1

    checked = ", ".join(str(path) for path in args.dockerfiles)
    print(
        f"Docker base-image digest pins are fresh "
        f"(max age: {args.max_age_days} days): {checked}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
