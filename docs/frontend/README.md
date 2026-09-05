# Frontend Developer Guide

Audience: engineer

What you need to navigate `web/src/`. Conventions are sketched in [`CLAUDE.md`](../../CLAUDE.md) → "Frontend conventions worth preserving"; this tree expands them.

## Stack

- Vite + React 18 + TypeScript (composite project — `tsconfig.json` references)
- TanStack Query for server state; no Redux / Zustand
- Tailwind utility CSS with a small project utility set in `web/src/index.css`
- Zod schemas mirror API responses (see [api-layer](api-layer.md))
- Vitest for unit/dom tests; Playwright for e2e
- Sentry for error reporting (gated on `VITE_SENTRY_DSN`)

## Pages

| File | Subject |
|---|---|
| [routing](routing.md) | Page tree, lazy-chunk boundaries, deep-link redirect handling |
| [api-layer](api-layer.md) | `lib/api.ts` (envelope unwrap, ApiError), Zod schemas, `categoryToUserMessage` |
| [tanstack-patterns](tanstack-patterns.md) | `["ws", wsId, …]` keys, invalidation helpers, `useApiMutation` |
| [components](components.md) | DataTable + reusable component catalog |
| [parts](parts.md) | Part-detail flows that span tabs and modals |
| [projects](projects.md) | Project-detail flows, including Source BOM |
| [reports](reports.md) | Report routes, including BOM buyability and replenishment cost |
| [label-designer](label-designer.md) | Label template designer (mm canvas, bindings) and the Print label actions |
| [tailwind-utilities](tailwind-utilities.md) | The `index.css` utility set (`btn`, `card`, `pill`, …) |
| [auth-flow](auth-flow.md) | `AuthProvider`, workspace switch, 401 redirect bus |
| [scanner](scanner.md) | ZXing / Scandit dual-mode + `bagCode.ts` parser |
| [testing](testing.md) | Vitest setup (`crypto.subtle` patch), dom tests, Playwright e2e |

## In-tree READMEs

Module-level orientation lives next to the code:

- `web/src/lib/README.md`
- `web/src/components/README.md`
- `web/src/pages/README.md`
