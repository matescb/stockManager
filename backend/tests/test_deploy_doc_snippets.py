"""Regression test: psql snippets in docs/deployment.md must expand
$POSTGRES_USER / $POSTGRES_DB inside the container, not the host shell.

Any line in deployment.md that contains `psql -U "$POSTGRES_USER"` must be
wrapped in a `sh -c '...'` invocation so the variable references are
evaluated by the shell running inside the container, where those variables
are actually set by the postgres image.
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
