from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core.auth import _WEAK_PASSWORDS

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSWORD_STRENGTH_TS = REPO_ROOT / "web" / "src" / "lib" / "passwordStrength.ts"

WEAK_PASSWORDS_SET_RE = re.compile(
    r"^[ \t]*(?:export\s+)?const\s+WEAK_PASSWORDS\s*=\s*new\s+Set(?:<[^>]+>)?\s*"
    r"\(\s*\[(?P<items>.*?)\]\s*\)\s*;",
    re.DOTALL | re.MULTILINE,
)


def _parse_fe_weak_passwords(source: str) -> list[str]:
    match = WEAK_PASSWORDS_SET_RE.search(source)
    if not match:
        pytest.fail(
            "Could not parse frontend WEAK_PASSWORDS. Expected "
            "`const WEAK_PASSWORDS = new Set([...]);` in "
            f"{PASSWORD_STRENGTH_TS}."
        )

    try:
        values = ast.literal_eval(f"[{match.group('items')}]")
    except (SyntaxError, ValueError) as exc:
        pytest.fail(f"Could not parse frontend WEAK_PASSWORDS string literals: {exc}")

    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        pytest.fail("Frontend WEAK_PASSWORDS must contain only string literals.")

    return values


def test_fe_be_weak_password_lists_match() -> None:
    fe_weak_passwords = _parse_fe_weak_passwords(
        PASSWORD_STRENGTH_TS.read_text(encoding="utf-8")
    )

    assert all(value == value.lower() for value in fe_weak_passwords)
    assert {value.lower() for value in fe_weak_passwords} == _WEAK_PASSWORDS


def test_parse_fe_weak_passwords_ignores_commented_out_definition() -> None:
    source = """
// const WEAK_PASSWORDS = new Set([
//   "commented-out-password",
// ]);
const WEAK_PASSWORDS = new Set([
  "real-password",
  "actual-password",
]);
"""

    assert _parse_fe_weak_passwords(source) == ["real-password", "actual-password"]
