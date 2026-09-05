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

import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_DIGEST_FRESHNESS_SCRIPT_PATH = (
    _REPO_ROOT / "scripts" / "check_docker_digest_freshness.py"
)
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


def _deploy_step_by_name(name: str) -> dict:
    data = _load()
    deploy = data["jobs"]["deploy"]
    for step in deploy["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"deploy job does not define step {name!r}")


def _deploy_host_key_step() -> dict:
    return _deploy_step_by_name("Verify deploy ED25519 host key")


def _deploy_ssh_step() -> dict:
    return _deploy_step_by_name("SSH deploy")


def test_deploy_fails_closed_when_password_pepper_missing_before_compose_up():
    step = _deploy_ssh_step()
    script = step["run"]

    assert "PASSWORD_PEPPER is missing or still the template placeholder" in script
    assert "make bootstrap-pepper" in script
    assert "secrets.token_hex" not in script
    assert script.index("PASSWORD_PEPPER") < script.index("docker compose")
    assert "logs --tail=120 backend" in script


def test_deploy_uses_raw_ssh_instead_of_appleboy():
    step = _deploy_ssh_step()
    script = step["run"]

    assert "uses" not in step
    assert "appleboy/ssh-action" not in str(step)
    assert "script_stop" not in step.get("with", {})
    assert "ssh -i" in script
    assert "-o StrictHostKeyChecking=yes" in script
    assert '-o UserKnownHostsFile="${DEPLOY_KNOWN_HOSTS}"' in script
    assert "-o HostKeyAlgorithms=ssh-ed25519" in script
    assert 'key.replace("\\\\n", "\\n")' in script
    assert 'ssh-keygen -y -f "${key_file}" > /dev/null' in script


def test_deploy_verifies_ed25519_host_fingerprint_before_raw_ssh():
    precheck = _deploy_host_key_step()
    deploy = _deploy_ssh_step()
    env = precheck.get("env", {})
    script = precheck["run"]

    assert env.get("DEPLOY_HOST_FINGERPRINT") == (
        "${{ secrets.DEPLOY_HOST_FINGERPRINT }}"
    )
    assert 'ssh-keyscan -T 10 -t ed25519 "${DEPLOY_HOST}"' in script
    assert 'actual_fingerprint=$(ssh-keygen -lf "${ed25519_known_hosts}"' in script
    assert '"${actual_fingerprint}" != "${DEPLOY_HOST_FINGERPRINT}"' in script
    assert 'ssh-keyscan -T 10 -t ecdsa "${DEPLOY_HOST}"' not in script
    assert "ecdsa_fingerprint" not in script
    assert 'echo "known_hosts=${ed25519_known_hosts}"' in script
    assert deploy["env"]["DEPLOY_KNOWN_HOSTS"] == (
        "${{ steps.deploy_host_key.outputs.known_hosts }}"
    )


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
# AUD-137 / issue #835: Docker base-image digest pins must not silently age out
# ---------------------------------------------------------------------------


def test_ci_has_docker_digest_freshness_gate():
    data = _load()
    job = data["jobs"].get("digest-freshness")
    assert job is not None, "missing digest-freshness CI job"

    runs = [step.get("run", "") for step in job["steps"] if "run" in step]
    assert any(
        "scripts/check_docker_digest_freshness.py --max-age-days 30" in run
        for run in runs
    ), "digest-freshness job does not run the 30-day Docker digest guard"

    deploy_needs = data["jobs"]["deploy"].get("needs") or []
    assert "digest-freshness" in deploy_needs, (
        "deploy must gate on digest-freshness so stale base images cannot ship"
    )


def test_docker_digest_freshness_script_rejects_stale_pin(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        textwrap.dedent(
            """\
            # Digest pinned on 2026-04-01; bump via Dependabot.
            FROM python:3.14@sha256:1111111111111111111111111111111111111111111111111111111111111111
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_DIGEST_FRESHNESS_SCRIPT_PATH),
            "--today",
            "2026-05-16",
            "--max-age-days",
            "30",
            str(dockerfile),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "digest pin is 45 days old; max is 30 days" in result.stderr


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


def test_compose_prod_cron_sidecars_have_shutdown_grace_period():
    """Cron sidecars must outlive their 600s job timeout during shutdown."""
    assert _COMPOSE_PROD_PATH.exists(), (
        f"missing compose file at {_COMPOSE_PROD_PATH}"
    )
    data = yaml.safe_load(_COMPOSE_PROD_PATH.read_text())
    services = data["services"]

    for service_name in (
        "backend-cron",
        "backend-cron-alerts",
        "backend-cron-sessions",
        "backend-cron-printing",
    ):
        assert services[service_name].get("stop_grace_period") == "605s"


def test_compose_prod_has_web_and_cron_healthchecks():
    """docker compose ps must expose health for web and cron sidecars."""
    assert _COMPOSE_PROD_PATH.exists(), (
        f"missing compose file at {_COMPOSE_PROD_PATH}"
    )
    data = yaml.safe_load(_COMPOSE_PROD_PATH.read_text())
    services = data["services"]

    web_healthcheck = services["web"].get("healthcheck", {})
    web_test = " ".join(web_healthcheck.get("test", []))
    assert "wget -qO- http://127.0.0.1/" in web_test

    cron_healthcheck = services["backend-cron"].get("healthcheck", {})
    cron_test = " ".join(cron_healthcheck.get("test", []))
    assert "find /tmp/stockmanager-job-heartbeats" in cron_test
    assert "sourcing-cache-sweep" in cron_test
    assert "-mmin -90" in cron_test

    alerts_healthcheck = services["backend-cron-alerts"].get("healthcheck", {})
    alerts_test = " ".join(alerts_healthcheck.get("test", []))
    assert "find /tmp/stockmanager-job-heartbeats" in alerts_test
    assert "sourcing-alerts-evaluate" in alerts_test
    assert "-mmin -90" in alerts_test


def test_compose_prod_backend_cron_sessions_disabled_jobs_stay_healthy():
    """backend-cron-sessions must set up shutdown and heartbeat guards."""
    assert _COMPOSE_PROD_PATH.exists(), (
        f"missing compose file at {_COMPOSE_PROD_PATH}"
    )
    data = yaml.safe_load(_COMPOSE_PROD_PATH.read_text())
    cron = data["services"]["backend-cron-sessions"]

    cmd = cron.get("command")
    assert isinstance(cmd, list), (
        "backend-cron-sessions.command must be a YAML list (JSON-array form)"
    )
    joined_cmd = " ".join(str(part) for part in cmd)
    mkdir = "mkdir -p /tmp/stockmanager-job-heartbeats"
    term_trap = "trap 'kill 0' TERM"
    int_trap = "trap 'kill 0' INT"
    session_branch = "if [ $$session_interval -gt 0 ]"
    reset_branch = "if [ $$reset_interval -gt 0 ]"
    session_interval = (
        "session_interval=$$(python -m app.cli.run_job "
        "session-purge --print-interval)"
    )
    reset_interval = (
        "reset_interval=$$(python -m app.cli.run_job "
        "password-reset-purge --print-interval)"
    )
    assert term_trap in joined_cmd
    assert int_trap in joined_cmd
    assert mkdir in joined_cmd
    assert session_interval in joined_cmd
    assert reset_interval in joined_cmd
    assert joined_cmd.index(term_trap) < joined_cmd.index(session_branch)
    assert joined_cmd.index(int_trap) < joined_cmd.index(reset_branch)
    assert joined_cmd.index(mkdir) < joined_cmd.index(session_branch)
    assert joined_cmd.index(mkdir) < joined_cmd.index(reset_branch)
    assert "$${SESSION_PURGE_INTERVAL_SECONDS" not in joined_cmd
    assert "$${PASSWORD_RESET_PURGE_INTERVAL_SECONDS" not in joined_cmd

    healthcheck = cron.get("healthcheck", {})
    healthcheck_test = " ".join(healthcheck.get("test", []))
    assert "--check-all-heartbeats session-purge password-reset-purge" in healthcheck_test
    assert healthcheck_test.count("python -m app.cli.run_job") == 1
    assert "--heartbeat-max-age-seconds 5400" in healthcheck_test
    assert "$${SESSION_PURGE_INTERVAL_SECONDS" not in healthcheck_test
    assert "$${PASSWORD_RESET_PURGE_INTERVAL_SECONDS" not in healthcheck_test


def test_prod_validate_times_backend_cron_sessions_probe():
    data = _load()
    prod_validate = data["jobs"]["prod-validate"]
    timing_step = next(
        (
            step
            for step in prod_validate["steps"]
            if step.get("name") == "Time backend-cron-sessions healthcheck probe"
        ),
        None,
    )

    assert timing_step is not None, (
        "prod-validate must time the backend-cron-sessions healthcheck probe"
    )
    run = timing_step.get("run", "")
    assert "docker exec" in run
    assert "--check-all-heartbeats session-purge password-reset-purge" in run
    assert "timeout_ms / 2" in run


def test_compose_prod_backend_cron_alerts_command_shape():
    """backend-cron-alerts must use the shared CLI scheduler path."""
    assert _COMPOSE_PROD_PATH.exists(), (
        f"missing compose file at {_COMPOSE_PROD_PATH}"
    )
    data = yaml.safe_load(_COMPOSE_PROD_PATH.read_text())
    cron = data["services"]["backend-cron-alerts"]
    cmd = cron.get("command")

    assert isinstance(cmd, list), (
        "backend-cron-alerts.command must be a YAML list (JSON-array form)"
    )
    joined = " ".join(str(part) for part in cmd)
    assert "python -m app.cli.run_job sourcing-alerts-evaluate" in joined
    assert "sleep 900" in joined
    assert "uvicorn" not in joined


def test_compose_prod_backend_cron_printing_command_shape():
    """backend-cron-printing must use the shared CLI scheduler path (ADR-0021).

    Two parallel subshell loops, mirroring backend-cron-sessions, so neither
    job's `timeout 600` can stack and push a run past stop_grace_period.
    """
    assert _COMPOSE_PROD_PATH.exists(), (
        f"missing compose file at {_COMPOSE_PROD_PATH}"
    )
    data = yaml.safe_load(_COMPOSE_PROD_PATH.read_text())
    cron = data["services"]["backend-cron-printing"]
    cmd = cron.get("command")

    assert isinstance(cmd, list), (
        "backend-cron-printing.command must be a YAML list (JSON-array form)"
    )
    joined = " ".join(str(part) for part in cmd)
    mkdir = "mkdir -p /tmp/stockmanager-job-heartbeats"
    dispatch = "python -m app.cli.run_job print-dispatch"
    reconcile = "python -m app.cli.run_job print-job-reconcile"

    assert "trap 'kill 0' TERM" in joined
    assert "trap 'kill 0' INT" in joined
    assert mkdir in joined
    assert f"timeout 600 {dispatch}" in joined
    assert f"timeout 600 {reconcile}" in joined
    # Dispatch on the tight operator-visible cadence; reconcile on the
    # _STALE_SENT_THRESHOLD cadence.
    assert "sleep 60;" in joined
    assert "sleep 300;" in joined
    assert joined.index(mkdir) < joined.index(dispatch)
    assert joined.index(mkdir) < joined.index(reconcile)
    assert "uvicorn" not in joined

    healthcheck = cron.get("healthcheck", {})
    healthcheck_test = " ".join(healthcheck.get("test", []))
    assert "find /tmp/stockmanager-job-heartbeats" in healthcheck_test
    assert "print-dispatch -mmin -10" in healthcheck_test
    assert "print-job-reconcile -mmin -30" in healthcheck_test
    # Shell-only probe: no Python cold start inside the 5s healthcheck timeout.
    assert "python" not in healthcheck_test


def test_compose_prod_print_host_defaults_to_empty():
    """PRINT_HOST must default to empty so an unconfigured deploy is a no-op.

    The printer is only reachable through a reverse-SSH tunnel + socat bridge
    + ufw rule that a human sets up on the VPS by hand. Giving PRINT_HOST a
    non-empty compose default would make every deploy start failing print jobs
    against a sink that does not exist yet.
    """
    assert _COMPOSE_PROD_PATH.exists(), (
        f"missing compose file at {_COMPOSE_PROD_PATH}"
    )
    data = yaml.safe_load(_COMPOSE_PROD_PATH.read_text())

    for service_name in (
        "backend",
        "backend-cron",
        "backend-cron-alerts",
        "backend-cron-sessions",
        "backend-cron-printing",
    ):
        env = data["services"][service_name]["environment"]
        assert env["PRINT_HOST"] == "${PRINT_HOST:-}", (
            f"{service_name} must inherit an empty-by-default PRINT_HOST"
        )
        assert env["PRINT_PORT"] == "${PRINT_PORT:-9100}"

    env_example = (_REPO_ROOT / "deploy" / ".env.prod.example").read_text()
    assert "\nPRINT_HOST=\n" in env_example, (
        "deploy/.env.prod.example must ship PRINT_HOST empty"
    )


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
