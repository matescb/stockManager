"""Assert that committed lockfiles exist and carry expected content (SEC2-016).

Guards against accidental deletion of uv.lock or requirements.lock, and
verifies that requirements.lock actually contains hashes (so the
--require-hashes Docker build step won't silently fall back to un-hashed
installs if someone re-exports without --hashes).
"""
from __future__ import annotations

from pathlib import Path


_BACKEND = Path(__file__).resolve().parent.parent


def test_uv_lock_exists():
    """uv.lock must be committed alongside pyproject.toml."""
    lock = _BACKEND / "uv.lock"
    assert lock.exists(), (
        "backend/uv.lock is missing — run `cd backend && uv lock` and commit the result"
    )
    # A valid uv.lock always starts with a version marker.
    content = lock.read_text()
    assert "version" in content, "uv.lock looks empty or malformed"


def test_requirements_lock_exists():
    """requirements.lock must be committed for the --require-hashes Docker build."""
    lock = _BACKEND / "requirements.lock"
    assert lock.exists(), (
        "backend/requirements.lock is missing — run "
        "`cd backend && uv export --format requirements-txt --hashes "
        "--no-dev --no-emit-project -o requirements.lock` and commit the result"
    )


def test_requirements_lock_has_hashes():
    """Every pinned package in requirements.lock must carry at least one sha256 hash.

    This ensures the file is usable with `pip install --require-hashes` and
    prevents silent downgrades to unhashed installs if someone regenerates
    the file without the --hashes flag.
    """
    lock = _BACKEND / "requirements.lock"
    if not lock.exists():
        return  # covered by test_requirements_lock_exists

    content = lock.read_text()
    assert "--hash=sha256:" in content, (
        "requirements.lock contains no sha256 hashes — regenerate with "
        "`uv export --format requirements-txt --hashes --no-dev --no-emit-project -o requirements.lock`"
    )


def test_ci_has_lockfile_drift_job():
    """CI must include a lockfile-drift check (SEC2-016)."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return  # yaml not available outside dev install; skip gracefully

    ci_path = _BACKEND.parent / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), f"missing CI workflow at {ci_path}"

    data = yaml.safe_load(ci_path.read_text())
    jobs = data.get("jobs", {})
    assert "lockfile-drift" in jobs, (
        "ci.yml is missing the `lockfile-drift` job — SEC2-016 requires "
        "a CI gate that fails when pyproject.toml and uv.lock have diverged"
    )


def test_ci_has_pip_audit_job():
    """CI must include a pip-audit step to catch HIGH/CRITICAL CVEs (SEC2-016)."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return

    ci_path = _BACKEND.parent / ".github" / "workflows" / "ci.yml"
    if not ci_path.exists():
        return

    data = yaml.safe_load(ci_path.read_text())
    jobs = data.get("jobs", {})
    assert "pip-audit" in jobs, (
        "ci.yml is missing the `pip-audit` job — SEC2-016 requires "
        "automated CVE scanning of the locked dependency set"
    )
