# Quantity Display

Audience: engineer

How the frontend renders an inventory quantity: one shared formatter, a
`DataTable` column factory that keeps sort and CSV numeric, and the rule
that no quantity is ever coerced to an integer for display.

This page is about **display only**. Quantity *inputs* are still
integer-only — see [Inputs are still integer-only](#inputs-are-still-integer-only).

## Background

Migration `0074` widened every quantity column to `Numeric(18, 6)` and gave
parts and ledger rows a unit code (`parts.unit_of_measure`,
`stock_entries.unit`). Quantities reach the wire through
`backend/app/domain/_quantity.py::quantity_out`, which renders a whole
value as a JSON **int** and a fractional one as a JSON **float**.

So a quantity arriving in the browser is a plain JSON number that may
have a fractional part, and its meaning ("12 what?") lives in a separate
`unit` field where the endpoint serialises one.

## The one helper

`web/src/lib/format.ts` — every quantity the UI shows goes through
`formatQuantity`.

```ts
import { formatQuantity } from "@/lib/format";

formatQuantity(12)                                   // "12"
formatQuantity(12.5, "m")                            // "12.5 m"
formatQuantity(0.1 + 0.2, "m")                       // "0.3 m"
formatQuantity(12, "pcs")                            // "12"
formatQuantity(12, "pcs", { alwaysShowUnit: true })  // "12 pcs"
formatQuantity(null, "m", { fallback: "—" })         // "—"
```

Three properties, all load-bearing (`web/src/lib/format.ts:79-104`):

1. **A whole quantity has no decimal tail.** The column is
   `Numeric(18, 6)`, so twelve pieces come back as twelve — not
   `12.000000`.
2. **A fractional quantity is exact.** Rendering rounds to six decimal
   places first, which is precisely what the column can store, so it
   removes binary-float artifacts (`0.1 + 0.2` is
   `0.30000000000000004` as a double) without ever hiding a stored digit.
3. **No thousands separators.** A quantity is routinely read back against
   a printed bag label or typed into an integer-only input; `10,000`
   matches neither.

Related exports: `formatQuantityNumber` (number only),
`quantityUnitSuffix` (the unit-visibility rule),
`formatQuantityPhrase` (for prose — see below).

## `pcs` is suppressed on screen

`quantityUnitSuffix` (`web/src/lib/format.ts:112-125`) renders nothing for
the default `pcs` unit. Discrete counts are the overwhelming default —
today they are the only case, since the unit is not yet user-settable —
so spelling it out would put a redundant " pcs" beside every quantity in
every table while telling an operator nothing they don't assume.
Suppressing it also makes a measured unit read as the exception it is:
`12` is twelve of something countable, `12.5 m` is unmistakably metres.

**The exception is print.** The pick-list sheet
(`web/src/routes/builds/picklist/PickListSheet.tsx`) is paper carried away
from the screen, where the reader has no page context to infer from, so it
passes `{ alwaysShowUnit: true }` and keeps printing `12 pcs`.

## Prose gets a noun

A sentence needs a noun where a table cell does not, so
`formatQuantityPhrase` says `"12 units"` / `"1 unit"` while the part is
counted, and swaps the noun for the unit code once it is measured —
`"12.5 m"`, because "12.5 metres units" is not a sentence. Used by the
activity timeline (`web/src/components/ActivityTimeline.tsx`) and the
receive / kitting toasts.

## `DataTable`: numeric accessor, formatted render

Use `quantityColumn` from `web/src/components/DataTable.tsx` for any
column showing a quantity.

```tsx
import { DataTable, quantityColumn } from "@/components/DataTable";

const columns = [
  quantityColumn<Part>({ key: "on_hand", header: "Stock", value: r => r.on_hand }),
  quantityColumn<Line>({
    key: "short_by",
    header: "Short by",
    value: r => r.short_by,
    render: text => <span className="text-danger">{text}</span>,
  }),
];
```

The factory exists because **both** of the obvious hand-rolled approaches
are wrong:

| Mistake | What breaks |
|---|---|
| Formatting inside `accessor` (`r => "12.5 m"`) | Sort compares accessor values with `<` / `>` (`DataTable.tsx:349-362`), so `"10 m"` sorts before `"9 m"`. `defaultAlignFor` (`:239-244`) right-aligns only a `number`. `cellText` (`:392-415`) puts the accessor straight into the CSV, giving a spreadsheet text it can't sum. |
| A `render` with no `accessor` | `cellText` has no safe way to read text out of a `ReactNode` and exports an **empty** CSV cell (`DataTable.tsx:410-414`). |

`quantityColumn` sets both halves: `accessor` returns the raw number
(sort, search, CSV export), `render` returns the formatted text with its
unit. It also pins `align: "right"` rather than letting `defaultAlignFor`
sample row 0, so a column whose first value happens to be `null` doesn't
left-align while the rest of the table right-aligns.

Pinned in `web/src/components/__dom__/DataTable.quantity.dom.test.tsx`:
numeric sort with fractional values, CSV cells staying numeric, the
render-only-column trap, and alignment with a null first row.

## Never coerce a quantity to an integer

`parseInt`, `Number.parseInt`, `| 0`, `~~`, `Math.floor`, `Math.trunc` and
`.toFixed(0)` all silently turn a 12.5 m bag into 12 — a wrong number an
operator then acts on. `| 0` is worse still: it is ToInt32, so a quantity
at or above 2³¹ wraps negative.

Rounding to the column's own scale (what `formatQuantity` does) is not the
same thing and is safe: six decimal places is exactly what the database can
hold.

Legitimate exceptions, all of them integer **by contract** rather than by
accident:

- **Build quantities.** `Build.quantity` stays `Integer` by explicit
  decision — you build 5 boards, not 5.5.
- **Distributor quantities.** Price breaks, MOQ and order multiples are an
  external supplier contract and are integer counts of purchasable
  packages.
- **Input parsing.** See below.

## Inputs are still integer-only

Quantity inputs (`<input type="number">`, their `onChange` parsers, and the
Zod `.int()` gates in `web/src/lib/schemas.ts`) deliberately still accept
whole numbers only. The server's Pydantic schemas are integer-only too, so
letting the UI send a fraction would only produce a 422.

Opening fractional input is a separate, deliberately later step: it is the
first thing that writes a fractional row, and the migration stays
reversible only until that happens. Don't relax an input gate as a
drive-by.

## See also

- [components](components.md) — the rest of the `DataTable` feature catalog
- [tailwind-utilities](tailwind-utilities.md) — `tabular-nums` and the table utilities
- `web/src/lib/README.md` — the `lib/` module map
