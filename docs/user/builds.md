# Builds — consume stock against a BOM

Audience: end user

A **build** says "I'm making N units of project X". The app calculates which parts you need, shows what's short, and decrements stock when you mark the build complete.

A build has a status:

- **planned** — created, parts reserved, nothing consumed yet.
- **in_progress** — same as planned for stock purposes; a status you can use to mark "I started".
- **complete** — stock has been decremented, build is closed.
- **cancelled** — build was abandoned, no stock decremented.

## Create a build

> _Screenshot: the New build form._

1. Click **Builds** in the sidebar.
2. Click **+ Build**.
3. Enter a **Name** (e.g. "BUILD-2026-001" or "Prototype run May").
4. Pick a **Project** — the build follows that project's BOM.
5. Set **Quantity** — how many units you're making. Required quantities are multiplied by this.
6. Optionally add **Comments**.
7. Click **Create build**.

You land on the build detail page. Required quantities are reserved against on-hand stock immediately.

## See what's short

> _Screenshot: the Shortage analysis card on the build page._

The **Shortage analysis** card lists every consumable BOM line with:

- **Required** — how many you need (BOM quantity × build quantity, plus any per-part attrition).
- **On hand** — current available stock for that part.
- **Substitutes (Σ)** — total available across substitute parts.
- **Short** — how many you can't cover. Red if non-zero.

If everything is green, you're ready to build. If anything is red:

- Order more stock — open the [Reports](reports.md) tab and use **Order shortages** on the BOM-shortage report to create a restock order in one click.
- Add a substitute — open the part, click **Substitutes**, add an interchangeable part. The shortage table will then count it.
- Reduce the build quantity.

## Plan consumption

Once you can cover the shortage, plan exactly what gets consumed.

> _Screenshot: the Consumption plan table with one row per BOM entry._

1. Click **Auto-fill** at the top right. The app fills in a sensible default: take from the main part first, fall back to a substitute if the main part is short.
2. Tweak the table:
   - **Part used** — pick the main part or one of its substitutes.
   - **Storage** — restrict consumption to one bin (or leave **— any —** to draw from anywhere).
   - **Qty** — how many to take from this row. Use **+ Sub** at the right to add another row for the same BOM entry (e.g. take 50 from the main + 20 from a substitute).
3. Repeat for each BOM line.

Each row goes onto the consumption plan; the same BOM entry can have several rows.

## Complete the build

1. Double-check the plan.
2. Click **Consume & complete build** at the bottom right.

Stock is decremented in one transaction. If anything fails (e.g. someone else just took the last of a part), the whole consume aborts and nothing is decremented — you'll see the error and can replan.

After success the build's status flips to **complete** and the consume controls disappear.

## Archive a build

Click **Archive** at the top right of the build page. Archived builds drop out of the main list. Restore from the **Archived** tab.

Reservations on a planned build are released when you archive or cancel it.

## What to do if it doesn't work

- **The Consume button gives "No lines"** — every row in the plan has Qty 0. Click **Auto-fill**, or fill in quantities by hand.
- **Consume fails with "insufficient stock"** — someone else moved or used parts since the page loaded. Reload the build page, re-check the shortage, and re-plan.
- **A BOM line marked DNP isn't in the plan** — that's deliberate. Do-not-place lines are recorded but not consumed.
- **A meta-part line has no parts to pick** — the meta-part has no members yet. Open the meta-part's **Members** tab and add at least one real part.
