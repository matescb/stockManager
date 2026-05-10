# Reports

Audience: end user

Four reports help you spot stock problems before they bite. They live under **Reports** in the sidebar.

The Reports landing page shows a strip of three tiles at the top — current low-stock count, total stock value, and lots expiring within 30 days. Click a tile to jump to the relevant view. Below the tiles are tabs for each one.

> _Screenshot: the Reports landing page with the KPI strip and the four tabs._

## Low-stock

Lists every part whose available quantity has dropped below the low-stock threshold you set in the part's settings.

Columns:

- **Part**, **MPN**, **Manufacturer**
- **On hand** — current quantity.
- **Reserved** — committed to planned/in-progress builds.
- **Available** — on hand minus reserved.
- **Threshold** — the value from the part's Settings tab.
- **Short by** — how many to order to reach threshold.

Parts with no threshold set don't appear; they're considered "no minimum".

### One-click restock

Click **Create restock order (N)** at the top right. The app:

1. Creates a new draft purchase order named `Restock <today's date>`.
2. Adds one line per shortage with the **Short by** quantity.
3. Opens the new order so you can set supplier, prices, and submit.

## Stock value

Sums the purchase cost of every unit currently on hand, grouped by currency and per part.

The page has two tables:

- **By currency** — total value in each currency (because the app doesn't convert between them).
- **By part** — every part with on-hand stock that has a unit price recorded. Currency is shown per row.

If most of your stock came in without a price (e.g. via Add stock with no price set), it doesn't appear here. Add prices when you record incoming stock to make this view useful.

## BOM shortage

Pretend you're about to build N units of a project. What would you be short of?

> _Screenshot: the BOM shortage page with project picker, build quantity, and shortage table._

1. Pick a **Project**.
2. Set **Build quantity**.
3. The table calculates the shortage per part, taking on-hand stock and substitutes into account.

Like the Low-stock view, you can click **Order shortages (N)** to spin a restock purchase order from the result with one click.

Use this before kicking off a real build — much cheaper to discover the problem here than mid-assembly.

## Expiring lots

Lists lots with on-hand stock that have an expiration date within a window. Defaults to 90 days; change the **Window (days)** input to widen or narrow.

Columns:

- **Lot** (links to the lot detail page)
- **Part**
- **On hand** — quantity in this lot.
- **Expires** — the expiration date.
- **Days** — days remaining. Already-expired lots show **expired** in red; lots inside 30 days are amber.

If you don't track lot expiration dates this view is empty. Add expiration dates when adding stock under **Lot name** + (TODO(verify-ui): the Add stock form's Lot section captures name and serial — confirm whether expiration date is a separate per-lot edit on the lot detail page rather than on Add stock).

## Sourcing risk

The **Sourcing risk** report lists parts whose current authorized supply needs attention. Use the filter chips to narrow the table to lifecycle, supply-chain, tariff, RoHS, price, stock, MOQ, lead-time, single-source, or preferred-distributor risks. The **Lifecycle** column shows the exact TrustedParts lifecycle text when available and sorts worst-first so obsolete, end-of-life, and last-time-buy parts rise above NRND, active, and blank rows.

## Export to CSV

Each table has an **Export CSV** option (in the column-toggle/search bar above the table) — useful for sharing with someone who doesn't have access to the workspace, or pasting into a spreadsheet for further filtering.

## What to do if it doesn't work

- **Low-stock is empty even though you're out of something** — you haven't set a low-stock threshold on the part. Open the part, click **Settings**, fill in **Low-stock report quantity**, save.
- **Stock value totals look way off** — most stock came in without a price. Add prices when receiving stock or when running Add stock.
- **BOM shortage shows no rows** — the project's BOM has no consumable lines (everything is DNP, or all lines are unmatched). Open the project's BOM tab to fix matches, then re-check.
- **Expiring lots is empty** — none of your lots have an expiration date set. The view only counts lots with both a date and remaining stock.
