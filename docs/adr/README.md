# Architecture Decision Records

Audience: engineer

Each ADR captures one decision: why it was made, what it forbids, what the alternatives were. The first 17 are retro-documented from the rules in `CLAUDE.md` ("Hard invariants" + "Things that have bitten us"). They are dated 2026-05-03 with status **Accepted (retro-documented from existing code)**.

New ADRs follow the template in [STYLE.md](../STYLE.md#adr-pages-docsadrnnnn-slugmd).

## Index

| # | Title |
|---|---|
| 0001 | [Append-only stock ledger](0001-append-only-stock-ledger.md) |
| 0002 | [Code-enforced workspace isolation](0002-code-enforced-workspace-isolation.md) |
| 0003 | [API envelope: `{ data, status }`](0003-api-envelope-data-status.md) |
| 0004 | [MPN uniqueness per workspace](0004-mpn-uniqueness-per-workspace.md) |
| 0005 | [Content-addressed asset storage](0005-content-addressed-assets.md) |
| 0006 | [Bag-signature normalization for scan idempotency](0006-bag-signature-normalization.md) |
| 0007 | [Provider catalog vs spec key split](0007-provider-catalog-vs-spec-split.md) |
| 0008 | [No `verify=False` on httpx clients](0008-no-tls-verify-false.md) |
| 0009 | [`docker-compose.prod.yml` `command:` is JSON-array form](0009-compose-prod-command-array-form.md) |
| 0010 | [`backend-init` one-shot service chowns `/data`](0010-backend-init-chown-one-shot.md) |
| 0011 | [Session cookie `secure` gated on `APP_ENV == "prod"`](0011-secure-cookie-env-gated.md) |
| 0012 | [uvicorn `--workers 1` for slowapi correctness](0012-uvicorn-single-worker-slowapi.md) |
| 0013 | [`--timeout-graceful-shutdown` < `stop_grace_period`](0013-graceful-shutdown-vs-stop-grace.md) |
| 0014 | [Sentry auth token must not enter Docker build context](0014-sentry-token-out-of-build-context.md) |
| 0015 | [Base images digest-pinned, Dependabot-rotated](0015-digest-pinned-base-images.md) |
| 0016 | [Sourcemaps emitted in CI only](0016-sourcemaps-ci-only.md) |
| 0017 | [Step-of-N PRs use `Refs #N`, not `Closes #N`](0017-step-of-n-prs-refs-not-closes.md) |
| 0018 | [Prod email verification requires SMTP; never log verification tokens](0018-prod-smtp-fail-closed.md) |
