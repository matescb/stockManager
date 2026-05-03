# ADR-0009: `docker-compose.prod.yml` `command:` is JSON-array form

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

The backend service's startup command is long: it runs `alembic upgrade head`, then `exec uvicorn …` with eight flags including `--proxy-headers --forwarded-allow-ips=*`. There are three YAML ways to express that:

1. JSON-array exec form: `command: ["sh", "-c", "alembic … && exec uvicorn … --flag --flag …"]`
2. YAML folded scalar (`>`) with continuation indentation, intended to fold newlines into spaces.
3. Multi-line block scalar (`|`) preserving newlines.

Form 2 was used historically. The folded scalar (`>`) **does not** fold newlines on indented continuation lines — it preserves them. The result was that `--proxy-headers --forwarded-allow-ips=*` was parsed as a separate (failing) shell command instead of arguments to `uvicorn`. Uvicorn started without `--proxy-headers`, so it didn't trust `X-Forwarded-For` from the nginx fronting it. slowapi (which buckets by client IP) bucketed every request under the docker bridge IP — the same IP for every user — and one chatty user globally rate-limited everyone else.

## Decision

The backend service's `command:` in `docker-compose.prod.yml` is the JSON-array exec form, on a single line:

```
command: ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=* --timeout-graceful-shutdown 25"]
```

(`docker-compose.prod.yml:148`). All other long commands in the file use the same form (`backend-init` at `:53` is the second example).

## Consequences

- **Good**: Argument boundaries are unambiguous — they are the spaces inside the single string passed to `sh -c`. There is no YAML scalar interpretation in the path between editor and shell.
- **Trade-offs**: The line is long and doesn't wrap nicely in a 100-column editor. Diffs on a flag change touch the whole line. Both are tolerable.
- **What it forbids**:
  - Don't reformat the `command:` to YAML folded scalar (`>`) or block scalar (`|`) — even if your editor offers to "wrap long lines for readability".
  - Don't split flags across lines with shell `\` continuations inside a YAML block scalar — same failure mode.
  - Don't move `--proxy-headers` or `--forwarded-allow-ips=*` out of the command line; they're load-bearing for slowapi correctness behind the nginx proxy.

## Alternatives considered

- **Block scalar (`|`)** with explicit newlines — rejected because the same parsing trap exists with a different YAML indicator: any indentation slip turns a flag into a separate command.
- **Move the command into an entrypoint shell script** in the image — viable, and arguably cleaner. Rejected for now because it would split deploy-relevant configuration (uvicorn flags) between the compose file and the image, and a flag change would require an image rebuild rather than a compose-only edit.

## References

- Source: `docker-compose.prod.yml:161` (backend command)
- Source: `docker-compose.prod.yml:53` (backend-init command)
- Incident context: slowapi bucketed every client by docker bridge IP after a folded-scalar reformat.
- Rule: `CLAUDE.md:131-136`
- Related: ADR-0012 (`--workers 1` for slowapi), ADR-0013 (graceful shutdown timing)
