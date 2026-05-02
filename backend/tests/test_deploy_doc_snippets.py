"""Regression tests: docs/deployment.md content assertions.

1. psql snippets must expand $POSTGRES_USER / $POSTGRES_DB inside the
   container, not the host shell. Any line that contains
   `psql -U "$POSTGRES_USER"` must be wrapped in a `sh -c '...'` invocation.

2. The Health endpoint section must document the two actual runtime checks
   performed by `GET /api/health` (SELECT 1 DB probe + UPLOAD_DIR write
   check) and must show both the 200 and 503 response shapes.
"""

import pathlib
import re

DOCS_ROOT = pathlib.Path(__file__).parents[2] / "docs"
DEPLOYMENT_MD = DOCS_ROOT / "deployment.md"

# Pattern that would be WRONG: psql -U "$POSTGRES_USER" NOT wrapped in sh -c
# We detect lines that contain the bare, un-wrapped form.
BARE_PSQL_RE = re.compile(r'psql\s+-U\s+"\$POSTGRES_USER"')
SH_C_PSQL_RE = re.compile(r"""sh\s+-c\s+['"].*psql\s+-U\s+"\$POSTGRES_USER""")


def test_psql_snippets_wrapped_in_sh_c() -> None:
    """Every psql -U "$POSTGRES_USER" occurrence must be inside sh -c '...'."""
    assert DEPLOYMENT_MD.exists(), f"{DEPLOYMENT_MD} not found"

    lines = DEPLOYMENT_MD.read_text().splitlines()
    violations: list[str] = []

    for lineno, line in enumerate(lines, start=1):
        if not BARE_PSQL_RE.search(line):
            continue
        # This line contains the pattern — verify it is part of a sh -c form.
        if not SH_C_PSQL_RE.search(line):
            violations.append(f"  line {lineno}: {line.strip()}")

    assert not violations, (
        "The following lines in docs/deployment.md use psql -U \"$POSTGRES_USER\" "
        "outside of a sh -c wrapper. Variables expand on the host shell, not inside "
        "the container.\n"
        + "\n".join(violations)
    )


def test_health_endpoint_section_exists() -> None:
    """docs/deployment.md must contain a ## Health endpoint section."""
    assert DEPLOYMENT_MD.exists(), f"{DEPLOYMENT_MD} not found"
    text = DEPLOYMENT_MD.read_text()
    assert "## Health endpoint" in text, (
        "docs/deployment.md is missing a '## Health endpoint' section. "
        "Add it to document the /api/health response shapes."
    )


def test_health_endpoint_documents_select1() -> None:
    """The Health endpoint section must mention the SELECT 1 DB probe."""
    assert DEPLOYMENT_MD.exists(), f"{DEPLOYMENT_MD} not found"
    text = DEPLOYMENT_MD.read_text()
    assert "SELECT 1" in text, (
        "docs/deployment.md does not mention 'SELECT 1'. "
        "The health handler runs SELECT 1 via SQLAlchemy — document it."
    )


def test_health_endpoint_documents_upload_dir() -> None:
    """The Health endpoint section must mention the UPLOAD_DIR write check."""
    assert DEPLOYMENT_MD.exists(), f"{DEPLOYMENT_MD} not found"
    text = DEPLOYMENT_MD.read_text()
    assert "UPLOAD_DIR" in text, (
        "docs/deployment.md does not mention 'UPLOAD_DIR'. "
        "The health handler checks os.access(UPLOAD_DIR, os.W_OK) — document it."
    )


def test_health_endpoint_documents_503_shape() -> None:
    """The Health endpoint section must document the 503 response body."""
    assert DEPLOYMENT_MD.exists(), f"{DEPLOYMENT_MD} not found"
    text = DEPLOYMENT_MD.read_text()
    # The 503 response must show the envelope structure: data null + status category
    assert '"data": null' in text or '"data":null' in text, (
        "docs/deployment.md does not show '\"data\": null' in the 503 example. "
        "The 503 response is envelope-wrapped with data: null."
    )
    assert "server_error" in text, (
        "docs/deployment.md does not mention 'server_error' category. "
        "503 responses use category 'server_error' in the envelope."
    )


def test_health_endpoint_documents_200_shape() -> None:
    """The Health endpoint section must document the 200 response body."""
    assert DEPLOYMENT_MD.exists(), f"{DEPLOYMENT_MD} not found"
    text = DEPLOYMENT_MD.read_text()
    assert '"status": "ok"' in text or '"status":"ok"' in text, (
        "docs/deployment.md does not show '\"status\": \"ok\"' in the 200 example. "
        "The 200 data payload contains {status: ok, db: ok, uploads: ok}."
    )
