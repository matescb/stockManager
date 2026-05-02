"""Pin the load-bearing shape of the CI workflow (TEST-014 / issue #116).

Pre-investigation in the issue's implementation plan confirmed that
`.github/workflows/ci.yml` already triggers on `pull_request:`, runs
pytest against Postgres-16, and gates `deploy` on green
`backend-tests` + `web-build`. This test makes that contract explicit
so a future workflow rewrite can't silently drop the PR gate (which
would let backend regressions ship to prod via the `main` push trigger).
"""
from __future__ import annotations

from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load() -> dict:
    assert _CI_PATH.exists(), f"missing CI workflow at {_CI_PATH}"
    return yaml.safe_load(_CI_PATH.read_text())


def test_ci_runs_on_pull_request():
    """Every PR must be gated by CI — not just `main` pushes."""
    data = _load()
    # PyYAML deserialises the bare `on:` key as Python's `True`.
    on = data.get("on") or data.get(True)
    assert on is not None, "no `on:` block in ci.yml"
    assert "pull_request" in on, "ci.yml does not trigger on pull_request"


def test_backend_tests_job_runs_pytest():
    data = _load()
    job = data["jobs"]["backend-tests"]
    runs = [step.get("run", "") for step in job["steps"] if "run" in step]
    assert any("pytest" in r for r in runs), (
        "no `pytest` invocation found in backend-tests steps"
    )


def test_web_build_job_exists():
    data = _load()
    assert "web-build" in data["jobs"], "missing web-build job"


def test_deploy_gates_on_green_main():
    data = _load()
    deploy = data["jobs"]["deploy"]
    cond = deploy.get("if", "")
    assert "refs/heads/main" in cond, "deploy is not pinned to main branch"
    assert "github.event_name == 'push'" in cond, (
        "deploy is not pinned to push events"
    )
    needs = deploy.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    assert "backend-tests" in needs, "deploy does not depend on backend-tests"
    assert "web-build" in needs, "deploy does not depend on web-build"
