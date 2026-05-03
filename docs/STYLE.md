# Documentation Style Guide

Audience: doc author (engineer or AI agent)

The rules every page in `docs/` follows. If a rule conflicts with what looks right for a specific page, ask before deviating.

## Header

Every file starts with:

```markdown
# <Title>

Audience: engineer            <!-- or "end user" -->

<one-line abstract — what this page is and isn't>
```

The audience tag is load-bearing. Engineers and end users read different shelves; the tag lets them (and tooling) skim the right tree.

## Tone

- Terse. State the fact, then move on. No "in this document, we will explore…".
- Engineer docs are dense and assume Python/TypeScript fluency, FastAPI/SQLAlchemy/React/TanStack familiarity, and that the reader can grep.
- End-user docs are short paragraphs, plain language, no jargon. Assume the reader is an engineering or warehouse operator who has used a web app before.
- No marketing language ("powerful", "seamlessly", "robust"). No emoji unless reproducing a UI element.

## Sources

- Cite source as `path:line` — e.g. `backend/app/domain/stock/service.py:142`. Prefer line ranges (`:142-160`) when discussing a block.
- If you assert a behaviour, cite the file that implements it. If you can't find the file, the assertion is wrong — don't write it.
- Linking another doc: relative path from current file. E.g. from `docs/api/stock.md` to an ADR: `../adr/0001-append-only-stock-ledger.md`.
- Anchor links use kebab-case headings (GitHub default). Verify the anchor exists.

## Don't restate

- The four canonical files own their material:
  - `docs/ARCHITECTURE.md` — stack, repo layout, ledger model, workspace isolation, API envelope, polymorphic tables, migrations.
  - `docs/development.md` — local dev, tests, lint baselines, migration workflow.
  - `docs/deployment.md` — prod architecture, CI/CD, ops, backups.
  - `CLAUDE.md` — hard invariants, "things that have bitten us", frontend conventions.
- If your topic is already there, link to the section instead of paraphrasing. Paraphrases drift; links don't.

## Code blocks

- Show real, copy-pasteable commands. Substitution placeholders are `<NAME>`, all caps.
- Backend commands: assume Docker compose unless explicitly outside-Docker. Pattern:
  ```
  docker compose -f docker-compose.dev.yml exec backend <cmd>
  ```
- Frontend commands: from `web/`. Pattern: `cd web && <cmd>`.
- For SQL/JSON output samples, show 2–4 lines, not the full payload. Use `…` for elision.

## Never invent

- If the source doesn't make a behaviour clear, write `TODO(verify): <question>` and move on. Do not guess.
- Do not extrapolate from one route to "all routes do X" unless you've checked.
- Do not list error codes / status codes unless you read them out of the code.

## Page templates

### API pages (`docs/api/<area>.md`)

```markdown
# <Area> API

Audience: engineer

<one-line abstract>

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination.

## Routes

### `METHOD /api/<path>`

<one-line summary>

**Request**

| Field | Type | Required | Notes |
|---|---|---|---|
| … | … | … | … |

**Response** — `200 OK` (envelope: `{ data, status }`)

```json
{ … }
```

**Errors** — `409 Conflict` returns `{ existing_id, existing_name }`. See [ADR-0004](../adr/0004-mpn-uniqueness-per-workspace.md).

**Notes**

- Source: `backend/app/api/routes/<file>.py:<line>`
- Service: `backend/app/domain/<dom>/service.py:<line>`
```

Repeat per route. Group related routes under a single H2 if it improves scanability.

### ADR pages (`docs/adr/NNNN-<slug>.md`)

```markdown
# ADR-NNNN: <Decision>

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

<why this decision was needed; what was being chosen between>

## Decision

<the decision, in 1–3 sentences>

## Consequences

- **Good**: …
- **Trade-offs**: …
- **What it forbids**: <the things future PRs must not do — this is often the load-bearing part>

## Alternatives considered

- **<alt 1>** — rejected because …
- **<alt 2>** — rejected because …

## References

- Source: `<path:line>`
- Related: `<path:line>`
- Phase doc: `docs/phases/<NN>-<name>.md` (if any)
```

### Runbook pages (`docs/runbooks/<scenario>.md`)

```markdown
# Runbook: <scenario>

Audience: engineer / on-call

- **When to run**: <trigger condition — alert name, symptom, scheduled cadence>
- **Severity**: SEV-1 / SEV-2 / SEV-3 / routine
- **Time-to-recovery target**: <minutes / hours>
- **Owner**: <team or @handle>

## Pre-flight

- Access checks: <SSH? gh? psql?>
- State checks: <"confirm X is true before starting">

## Steps

1. …
2. …

## Verification

- <how you know the issue is resolved — concrete signal>

## Rollback

- <if the steps make things worse, how to undo>

## Post-mortem prompts

- Was the trigger clear?
- Did this runbook match reality?
- What edit would have helped?
```

### Domain pages (`docs/domain/<entity>.md`)

Free-form within the audience/abstract/citations rules. ER snippets in mermaid. Service entry points listed as a table:

```markdown
| Operation | Entry point | Notes |
|---|---|---|
| Compute current quantity | `domain/stock/service.py::current_quantity` | Single-part read; bulk variant exists. |
```

### Frontend pages (`docs/frontend/<topic>.md`)

Free-form. Code samples in TypeScript with file path comments:

```ts
// web/src/lib/api.ts
export function get<T>(path: string): Promise<T> { … }
```

### User pages (`docs/user/<topic>.md`)

- H1 is the user's task ("Receive an order"), not the feature name ("Order receive workflow").
- Steps are numbered, one verb per step.
- Screenshot placeholders: `> _Screenshot: <description of what to capture>_`
- "What to do if it doesn't work" section at the bottom for common failure modes (1–3 bullets each).

## Anti-patterns

- "This is a comprehensive guide to…"  →  delete; the audience tag and abstract already say so.
- "Note that…" / "Important:"  →  delete the prefix; just state the thing.
- Walls of prose between code blocks  →  break them up; engineers scan.
- Restating the API envelope in every page  →  link to `docs/api/README.md`.
- Tutorials longer than the feature  →  split.
- Forward-promises ("we plan to add…")  →  out. Docs describe what is, not what might be.
