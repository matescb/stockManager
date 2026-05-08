"""Pin the load-bearing shape of the CI workflow (TEST-014 / issue #116).

Pre-investigation in the issue's implementation plan confirmed that
`.github/workflows/ci.yml` already triggers on `pull_request:`, runs
pytest against Postgres-16, and gates `deploy` on green
`backend-tests` + `web-build`. This test makes that contract explicit
so a future workflow rewrite can't silently drop the PR gate (which
would let backend regressions ship to prod via the `main` push trigger).

INFRA-004: also asserts that the backend-tests job uses `uv sync` (not
bare `pip install -e`) and that a `uv lock --check` freshness step is
present.

Also covers INFRA-003 / issue #44: asserts that the Sentry auth token is
never exposed as a Docker build arg and that sourcemap upload happens
in CI only.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_UV_LOCK_PATH = _REPO_ROOT / "backend" / "uv.lock"
_COMPOSE_PROD_PATH = _REPO_ROOT / "docker-compose.prod.yml"
_DOCKERFILE_PROD_PATH = _REPO_ROOT / "web" / "Dockerfile.prod"


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


# ── INFRA-004: reproducible backend builds via uv lockfile ──────────────────


def test_uv_lock_file_exists():
    """backend/uv.lock must be committed so builds are reproducible."""
    assert _UV_LOCK_PATH.exists(), (
        f"backend/uv.lock not found at {_UV_LOCK_PATH}; "
        "run `cd backend && uv lock` and commit the result"
    )


def test_ci_uses_uv_sync_not_pip_install():
    """backend-tests job must install via `uv sync`, not bare `pip install -e`."""
    data = _load()
    job = data["jobs"]["backend-tests"]
    runs = [step.get("run", "") for step in job["steps"] if "run" in step]
    all_run_text = "\n".join(runs)
    assert "uv sync" in all_run_text, (
        "backend-tests job does not use `uv sync`; update ci.yml"
    )
    assert "pip install -e" not in all_run_text, (
        "backend-tests job still uses `pip install -e`; replace with `uv sync`"
    )


def test_ci_has_uv_lock_check_step():
    """backend-tests job must have a `uv lock --check` freshness step."""
    data = _load()
    job = data["jobs"]["backend-tests"]
    runs = [step.get("run", "") for step in job["steps"] if "run" in step]
    assert any("uv lock --check" in r for r in runs), (
        "no `uv lock --check` step found in backend-tests job; "
        "add it so CI fails when uv.lock is stale"
    )


# ---------------------------------------------------------------------------
# INFRA-003 / issue #44: Sentry auth token must never enter the Docker build
# ---------------------------------------------------------------------------


def test_compose_prod_web_build_args_no_sentry_token():
    """docker-compose.prod.yml must NOT pass SENTRY_AUTH_TOKEN as a build arg.

    The token must only live in GitHub Actions secrets so it never enters
    the Docker layer cache (INFRA2-010).
    """
    assert _COMPOSE_PROD_PATH.exists(), (
        f"missing compose file at {_COMPOSE_PROD_PATH}"
    )
    data = yaml.safe_load(_COMPOSE_PROD_PATH.read_text())
    web_build = data["services"]["web"]["build"]
    args = web_build.get("args", {})
    # args may be a dict or a list of "KEY=value" strings; normalise to names.
    if isinstance(args, list):
        arg_names = {a.split("=")[0] for a in args}
    else:
        arg_names = set(args.keys())
    sensitive = {"SENTRY_AUTH_TOKEN", "SENTRY_ORG", "SENTRY_PROJECT"}
    leaked = sensitive & arg_names
    assert not leaked, (
        f"docker-compose.prod.yml web build.args contains sensitive Sentry "
        f"vars that must live only in GitHub Actions secrets: {leaked}"
    )


def test_compose_prod_backend_cron_command_shape():
    """backend-cron must stay on the shared CLI scheduler path."""
    assert _COMPOSE_PROD_PATH.exists(), (
        f"missing compose file at {_COMPOSE_PROD_PATH}"
    )
    data = yaml.safe_load(_COMPOSE_PROD_PATH.read_text())
    cron = data["services"]["backend-cron"]
    cmd = cron.get("command")

    assert isinstance(cmd, list), (
        "backend-cron.command must be a YAML list (JSON-array form)"
    )
    joined = " ".join(str(part) for part in cmd)
    assert "python -m app.cli.run_job sourcing-cache-sweep" in joined
    assert "uvicorn" not in joined


def test_dockerfile_prod_no_sentry_token_arg():
    """web/Dockerfile.prod must NOT declare ARG SENTRY_AUTH_TOKEN.

    Declaring the ARG (even without a default) would allow the token to be
    passed in via `docker build --build-arg` and baked into a layer.
    """
    assert _DOCKERFILE_PROD_PATH.exists(), (
        f"missing Dockerfile at {_DOCKERFILE_PROD_PATH}"
    )
    content = _DOCKERFILE_PROD_PATH.read_text()
    for line in content.splitlines():
        stripped = line.strip()
        # Allow commented-out lines (e.g. documentation notes about why it
        # was removed) but reject any live ARG declaration.
        if stripped.startswith("#"):
            continue
        assert "ARG SENTRY_AUTH_TOKEN" not in stripped, (
            "web/Dockerfile.prod has a live `ARG SENTRY_AUTH_TOKEN` line; "
            "the token must not enter the Docker build context (INFRA2-010)"
        )


def test_ci_web_build_has_sourcemap_upload_step():
    """The web-build CI job must contain a sourcemap-upload step for Sentry.

    The step must be gated on push-to-main and must use the auth token from
    GitHub Actions secrets, not from build args.
    """
    data = _load()
    web_build_steps = data["jobs"]["web-build"]["steps"]
    upload_steps = [
        s for s in web_build_steps
        if "sourcemap" in s.get("name", "").lower()
        or "sentry" in s.get("name", "").lower()
    ]
    assert upload_steps, (
        "web-build job has no sourcemap/sentry upload step; "
        "add one gated on push to main (INFRA2-010)"
    )
    # At least one upload step must be gated on push to main.
    main_gated = [
        s for s in upload_steps
        if "refs/heads/main" in s.get("if", "")
    ]
    assert main_gated, (
        "sourcemap upload step is not gated on `refs/heads/main`; "
        "PRs must not upload sourcemaps (they'd pin maps to a SHA that "
        "never ships)"
    )
