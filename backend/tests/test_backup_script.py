"""Assert deploy/backup.sh and deploy/.env.prod.example contain the
dead-man's-switch env vars introduced in INFRA-006 (issue #47).

These are static-file checks — no database required.
"""
from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKUP_SH = _REPO_ROOT / "deploy" / "backup.sh"
_ENV_EXAMPLE = _REPO_ROOT / "deploy" / ".env.prod.example"


def _read(path: Path) -> str:
    assert path.exists(), f"missing file: {path}"
    return path.read_text()


def test_backup_sh_declares_ok_url_var():
    assert "BACKUP_HEALTHCHECK_OK_URL" in _read(_BACKUP_SH), (
        "deploy/backup.sh must declare BACKUP_HEALTHCHECK_OK_URL"
    )


def test_backup_sh_declares_fail_url_var():
    assert "BACKUP_HEALTHCHECK_FAIL_URL" in _read(_BACKUP_SH), (
        "deploy/backup.sh must declare BACKUP_HEALTHCHECK_FAIL_URL"
    )


def test_backup_sh_has_trap_err():
    content = _read(_BACKUP_SH)
    assert "trap" in content and "ERR" in content, (
        "deploy/backup.sh must contain 'trap ... ERR' for failure alerting"
    )


def test_backup_sh_pings_ok_url_on_success():
    content = _read(_BACKUP_SH)
    # The success ping must reference the OK URL variable after the last echo.
    assert "BACKUP_HEALTHCHECK_OK_URL" in content, (
        "deploy/backup.sh must ping BACKUP_HEALTHCHECK_OK_URL on success"
    )
    # curl must be used to perform the ping.
    assert "curl" in content, (
        "deploy/backup.sh must use curl to send healthcheck pings"
    )


def test_env_example_documents_ok_url():
    assert "BACKUP_HEALTHCHECK_OK_URL" in _read(_ENV_EXAMPLE), (
        "deploy/.env.prod.example must document BACKUP_HEALTHCHECK_OK_URL"
    )


def test_env_example_documents_fail_url():
    assert "BACKUP_HEALTHCHECK_FAIL_URL" in _read(_ENV_EXAMPLE), (
        "deploy/.env.prod.example must document BACKUP_HEALTHCHECK_FAIL_URL"
    )
