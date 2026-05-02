"""
SEC2-018 — Tests for check_no_traceback_leaks.py.

Happy path: the real backend/app/ tree contains no HTTPException that leaks
stack-trace data.

Negative path: a synthetic .py file that *does* contain the forbidden pattern
makes the script exit with code 1.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Absolute path to the checker script so tests are independent of cwd.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_no_traceback_leaks.py"


def _run_script(*extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *extra_args],
        capture_output=True,
        text=True,
    )


def test_clean_app_tree_passes():
    """The real backend/app/ directory must have no traceback leaks."""
    result = _run_script()
    assert result.returncode == 0, (
        f"check_no_traceback_leaks found violations in backend/app/:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_offending_file_detected(tmp_path: Path):
    """A .py file with HTTPException(detail=traceback.format_exc()) must be caught."""
    import importlib.util

    bad_py = tmp_path / "bad_module.py"
    bad_py.write_text(
        textwrap.dedent(
            """\
            import traceback
            from fastapi import HTTPException

            def bad_handler():
                raise HTTPException(
                    status_code=500,
                    detail={"trace": traceback.format_exc()},
                )
            """
        )
    )

    # Load checker via importlib so we don't need scripts/ on sys.path.
    spec = importlib.util.spec_from_file_location("checker", _SCRIPT)
    assert spec is not None
    checker = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(checker)  # type: ignore[union-attr]

    violations = checker.check_file(bad_py)
    assert violations, (
        "Expected check_file() to return violations for a file containing "
        "traceback.format_exc() in HTTPException(detail=…), but got none."
    )
    # The violation should mention the leak keyword somewhere in the snippet.
    _, snippet = violations[0]
    assert "format_exc" in snippet or "traceback" in snippet


def test_clean_file_not_flagged(tmp_path: Path):
    """A file with a normal HTTPException(detail={…}) must not be flagged."""
    good_py = tmp_path / "good_module.py"
    good_py.write_text(
        textwrap.dedent(
            """\
            from fastapi import HTTPException

            def raise_404(name: str):
                raise HTTPException(
                    status_code=404,
                    detail={"message": f"Part {name!r} not found"},
                )
            """
        )
    )

    # Import and call check_file directly.
    import importlib.util

    spec = importlib.util.spec_from_file_location("checker", _SCRIPT)
    assert spec is not None
    checker = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(checker)  # type: ignore[union-attr]

    violations = checker.check_file(good_py)
    assert violations == [], f"Unexpected violations in clean file: {violations}"
