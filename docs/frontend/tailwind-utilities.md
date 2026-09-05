# Tailwind utilities

Audience: engineer

The project utility set defined in `web/src/index.css` (CLAUDE.md
"Frontend conventions worth preserving" calls these out as load-bearing).
Use these classes before adding new ones — the visual language depends on
them.

## Design tokens

`web/src/index.css:5-46`. Everything is driven by CSS custom properties
defined under `@layer base`. Each is an `R G B` triplet so Tailwind can
wrap them in `rgb()` / `rgb(... / opacity)`.

| Token | Light value | Dark value | Used for |
|---|---|---|---|
| `--c-bg` | `248 250 252` | `11 12 15` | App background |
| `--c-panel` | `255 255 255` | `20 22 27` | Card / panel background |
| `--c-panel2` | `241 245 249` | `28 31 37` | Secondary panel, button surface |
| `--c-border` | `226 232 240` | `31 34 41` | Subtle dividers |
| `--c-borderStrong` | `203 213 225` | `42 45 52` | Button border, table outline |
| `--c-rowHover` | `248 250 252` | `21 24 31` | `tr:hover` background |
| `--c-panelHover` | `226 232 240` | `38 42 50` | Button hover surface |
| `--c-text` | `15 23 42` | `230 230 230` | Primary text |
| `--c-muted` | `100 116 139` | `154 160 172` | Secondary text, labels |
| `--c-accent` | `22 163 74` | `74 222 128` | Brand green |
| `--c-accentHover` | `21 128 61` | `34 197 94` | Accent hover |
| `--c-danger` | `220 38 38` | `248 113 113` | Errors, destructive actions |
| `--c-warning` | `217 119 6` | `251 191 36` | Warning state |
| `--c-success` | `22 163 74` | `52 211 153` | Success state |

Dark mode is gated by `:root.dark` (`index.css:23-38`). The class is
toggled imperatively by `bootTheme()` (`web/src/lib/theme.tsx:38-43`)
before React mounts, to avoid a flash of the wrong theme. The Tailwind
config wires the tokens as named colours (`bg-bg`, `text-muted`,
`border-borderStrong`, etc.) so the palette is the same in either mode —
TODO(verify): exact `tailwind.config.js` mapping.

## Component utility set (`@layer components`)

`web/src/index.css:48-116`.

### Buttons

| Class | Definition source | When to use |
|---|---|---|
| `.btn` | `index.css:49-51` | Default neutral button — bordered surface |
| `.btn-primary` | `index.css:52-54` | Affirmative action, accent-tinted |
| `.btn-danger` | `index.css:55-57` | Destructive action, red-tinted |
| `.btn-ghost` | `index.css:58-60` | Transparent button, hover-fills to `panel2` |
| `.btn-sm` | `index.css:61-63` | Modifier — smaller padding + xs text |

`.btn-sm` is the only compact-button mechanism. Do **not** write
`btn text-xs` — it shrinks the type but leaves `.btn`'s full `px-3 py-1.5`
padding, so the button reads as a full-size control with small text. 24
sites did this before the typographic-scale pass.

All four destructive/affirmative variants `@apply btn` first, then layer
the colour, so border + focus ring stay consistent
(`index.css:53`, `:56`, `:59`).

```html
<!-- combine modifier with variant -->
<button class="btn-primary btn-sm">Save</button>
<button class="btn-danger">Delete</button>
<button class="btn-ghost btn-sm" aria-label="Toggle density">…</button>
```

### Forms

| Class | Source | Notes |
|---|---|---|
| `.input` | `index.css:64-66` | Single-line text input. Width 100% by default. |
| `.label` | `index.css:67-69` | Uppercase tracked label rendered above the field |

Both standardise focus-ring (`focus:ring-2 focus:ring-accent/40`) so a
`<select>` styled with `.input` matches a `<input class="input">`
visually.

### Headings

`index.css:73-97`. Tailwind's default fontSize scale is
`xs / sm / base / lg / xl / 2xl …` — **there is no `md` step**, and
`tailwind.config.js` extends only `colors` and `fontFamily`, so it never
adds one. Preflight additionally flattens `h1`–`h6` to
`font-size: inherit`, so a heading is only a heading if a class says so.
These three utilities are the entire scale.

| Class | Source | Size | When to use |
|---|---|---|---|
| `.page-title` | `index.css:89-91` | 20px/28 semibold | The topmost title of a route — one per page |
| `.card-title` | `index.css:92-94` | 16px/20 semibold | Section heading inside a `card` or panel |
| `.section-title` | `index.css:95-97` | 12px uppercase muted | Eyebrow over a label-ish group |

Body copy is 14px (`text-sm`), so the ladder is **20 / 16 / 14 / 12** —
modest, clearly separated steps sized for a dense operator tool rather
than a marketing page. `.card-title` carries `leading-tight` on purpose:
at 16px that is a 20px line box, the same height as the 14px/20px it
replaced, so section titles gained size and weight without costing a
pixel of vertical space.

Do not hand-roll `text-xl font-semibold` on an `<h1>` or invent a new
step — use the utility, so the scale stays tunable from one place.

### Surfaces

| Class | Source | Notes |
|---|---|---|
| `.card` | `index.css:70-72` | Bordered, rounded panel — the wrapping container for almost every section |
| `.kbd` | `index.css:98-100` | Inline keyboard-key chip used in the command palette and tooltips |
| `.pill` | `index.css:113-115` | Small rounded badge for status / count tags |

### Table

`.table` (`index.css:101-112`) is the shared base — `<DataTable>` uses it
internally. Header cells get uppercase tracked muted text, body cells get
horizontal padding + subtle border. Hover highlights the row via
`--c-rowHover`.

```html
<table class="table">
  <thead>
    <tr><th>Name</th><th>Qty</th></tr>
  </thead>
  <tbody>
    <tr><td>Cap</td><td class="text-right tabular-nums">42</td></tr>
  </tbody>
</table>
```

### Body defaults

`web/src/index.css:40-46`. The body sets `font-feature-settings: "tnum"`
so digits are tabular by default — number columns line up without an
explicit `tabular-nums`. Cards and inputs inherit this; opt out with
`font-variant-numeric: normal` when proportional digits look better
(rare).

## When to add a new class vs use existing

CLAUDE.md → "Frontend conventions worth preserving" pins this:

> Use those before adding new ones.

In practice that means:

- Need a button? Use `btn` / `btn-primary` / `btn-danger` /
  `btn-ghost` (+ `btn-sm`). Do not write `bg-blue-500` /
  `bg-red-600` / etc. — they bypass the dark-mode token system. The same
  applies to text: error copy is `text-danger`, never `text-red-600`.
- Need a heading? Use `page-title` / `card-title` / `section-title`.
  Never invent a size step — Tailwind has no `md`, and a class that
  isn't in the config compiles to nothing and fails silently.
- Need a panel? `card`. Need a sub-region inside one? Add structural
  Tailwind utilities (`p-4`, `flex`, `gap-2`) to a `card`.
- Need a status chip? `pill`, optionally tinted via colour utilities
  (`text-danger`, `bg-warning/10`, …).
- Need a label? `label` above the input.

If a use case really doesn't fit, the bar is "would three or more sites
need this exact class?" If yes, add it under `@layer components` so the
token system and dark mode follow. If not, inline Tailwind utilities at
the call site.

## Stale-token regression (FE2-009)

The scanner pre-fix used `bg-bg-soft` and `text-text-muted` — neither
existed in `tailwind.config.js`. The panel rendered transparent and
inherited text colour. The fix used the defined tokens (`bg-panel2`,
`text-muted`); see comments at `web/src/components/scanner/Scanner.tsx:60-62`
and `web/src/components/scanner/ZxingScanner.tsx:461-463`. The lesson:
read the token list above before guessing a class name.

## Command palette overrides

`web/src/index.css:118-162` styles `cmdk`'s `data-*` attributes
(`[cmdk-overlay]`, `[cmdk-dialog]`, `[cmdk-root]`, `[cmdk-input]`,
`[cmdk-list]`, `[cmdk-empty]`, `[cmdk-group-heading]`, `[cmdk-item]`).
These aren't reusable utilities — they're library-specific selectors
that pin the palette to the project's design tokens. If you replace
`cmdk`, this block goes with it.

## `cn(…)` utility

`web/src/lib/cn.ts:1-6`. Wrapper around `clsx` + `tailwind-merge` so
later classes override earlier ones safely:

```ts
// web/src/lib/cn.ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)); }
```

Use it whenever you compose conditional classes — `tailwind-merge`
de-duplicates conflicting utilities (`p-2` + `p-4` → `p-4`), which raw
template strings can't do.

## TODO(verify)

- `web/tailwind.config.js` — confirm the exact mapping from CSS custom
  properties to Tailwind colour names. The palette references above
  (`bg-bg`, `bg-panel`, `text-muted`, `border-border`, `text-accent`,
  …) work in the codebase, but the doc here would be more useful if it
  could enumerate them precisely.
