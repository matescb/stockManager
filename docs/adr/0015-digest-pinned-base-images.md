# ADR-0015: Base images digest-pinned, Dependabot-rotated

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

`FROM python:3.12-slim` resolves to whatever digest Docker Hub considers the latest `3.12-slim` at build time. Two builds an hour apart can produce different runtime layers, and a compromised registry push could swap the layer under us silently. The mitigation is to pin to an immutable content digest (`@sha256:…`) so the build is deterministic and one layer-change is one PR diff.

Pinning has the obvious downside: a manual pin rots, and now the base image's CVE patches don't land. The two requirements — pin for determinism, rotate for patches — meet at a Dependabot weekly rotation that opens a PR per digest bump. CI exercises the new digest before merge; if anything regresses, the PR doesn't land.

## Decision

Every `FROM` in the repo's Dockerfiles is digest-pinned with `@sha256:<digest>`, and the line above carries a `# Digest pinned on YYYY-MM-DD` comment. Current pins:

- `backend/Dockerfile:4` — `python:3.14@sha256:0ba00180…` (builder stage)
- `backend/Dockerfile:28` — `python:3.14-slim@sha256:5b3879b6…` (runtime)
- `web/Dockerfile.prod:11` — `node:25-alpine@sha256:bdf2cca6…` (build stage)
- `web/Dockerfile.prod:43` — `nginx:alpine@sha256:56168782…` (runtime)

(Versions and digests are correct as of 2026-05-03. Dependabot rotates them; this list is illustrative — read the Dockerfile for the live pin.)

Dependabot is configured to open weekly PRs on Mondays for both `/backend` and `/web` Docker contexts (`.github/dependabot.yml`). CI's backend-tests + web-build jobs exercise the new digest, so a regression surfaces before merge.

Manual digest bump (when Dependabot is too slow):

```
curl -s https://registry.hub.docker.com/v2/repositories/library/<image>/tags/<tag> \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['digest'])"
```

Then update the `@sha256:` line and the `# Digest pinned on` comment.

## Consequences

- **Good**: Builds are reproducible. A registry compromise that swaps the `:3.12-slim` tag does not affect us — the digest stays the same. CVE patches still land via Dependabot's weekly cycle.
- **Trade-offs**: A weekly PR per Dockerfile, four PRs in steady state. Reviewing them is mechanical (CI green → merge), but they are extra noise.
- **What it forbids**:
  - Don't loosen any `FROM` line to a bare tag (`FROM python:3.12-slim`).
  - Don't pin a digest without the `# Digest pinned on YYYY-MM-DD` comment — the comment is how reviewers spot stale pins.
  - Don't disable Dependabot's Docker rotation in `.github/dependabot.yml`. The rotation is what keeps the pin from rotting.
  - Don't pin to a multi-arch tag's manifest-list digest if a single-arch digest would do — the manifest-list digest changes when any architecture is republished, defeating reproducibility for our arch.

## Alternatives considered

- **Bare tags + frequent rebuilds** — rejected because builds are non-deterministic and a registry compromise propagates immediately.
- **Pin to a specific patch tag (e.g. `python:3.12.7-slim`)** — partial mitigation; the tag is more stable than `3.12-slim` but still mutable. Digest is the only immutable reference.

## References

- Source: `backend/Dockerfile:4`, `:28`
- Source: `web/Dockerfile.prod:11`, `:43`
- Source: `.github/dependabot.yml`
- Rule: `CLAUDE.md:170-175` (INFRA2-015)
