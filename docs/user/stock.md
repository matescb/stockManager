# Add, remove, and move stock

Audience: end user

Track quantity changes. View history. Use lots when you need to know which batch a part came from.

Stock is **append-only**: every change is recorded as a new row in history. You never edit a number directly; you record the event ("added 100", "removed 5", "moved 50 to bin C") and the running total updates.

## See what's on hand

Open a part and click the **Stock** tab.

> _Screenshot: the Stock tab showing total on hand and a breakdown by storage / lot._

The page shows:

- **Total on hand** — the part's grand total across all bins and lots.
- A table breaking it down by **Storage** and **Lot**. A row per (storage, lot) combination.

If you don't use lots, the Lot column shows "—" and rows are grouped only by storage.

## Add stock

1. Open a part.
2. Click the **Add stock** tab.
3. Enter a **Quantity** (positive integer).
4. Pick a **Storage location**. If the part has a default location and "mandatory" is on, pick that one.
5. Optionally fill **Price** — pick **Per component** for unit price, or **Entire lot** for total. Set the **Currency**.
6. Optionally enter a **Lot name** and **Serial number**. Skip both if you don't track lots.
7. Optionally add **Comments** (free text — useful for "from PO 2026-001").
8. Click **Add**.

> _Screenshot: the Add stock form with price mode set to Per component._

Most stock arrives via the [scan-to-import](scan-import.md) flow or via [purchase orders](orders.md). Use **Add stock** for ad-hoc additions: a part you found in a drawer, samples, returns from a build.

## Remove stock

1. Open a part.
2. Click the **Remove stock** tab.
3. Pick a **Source** — the dropdown lists every (storage, lot) combination that has stock. The label shows the location, the lot name (if any), and the current quantity.
4. Enter a **Quantity** up to the source's max.
5. Add **Comments** if helpful.
6. Click **Remove**.

> _Screenshot: the Remove stock form with the Source dropdown open._

If nothing is on hand, the page tells you so and the Remove button stays disabled.

## Move stock

Move shifts a quantity from one bin to another without changing the running total.

1. Open a part.
2. Click the **Move stock** tab.
3. Pick a **From** source (same dropdown as Remove).
4. Pick a **To storage**.
5. Enter a **Quantity**.
6. Tick **Split lot at destination** if you're tracking lots and want the moved amount to become a new sub-lot at the destination. Most users leave it off.
7. Click **Move**.

## Lots vs untracked

A **lot** is a batch with a name and (optionally) a serial number. Use lots when you need to trace which production run a unit came from — required for some regulated industries, and useful for expiring components.

- If you leave **Lot name** blank when adding stock, the stock is **untracked** — it gets a system-generated lot record under the hood, but the UI hides it.
- If you set a lot name, that name appears in the Stock breakdown, in Remove/Move pickers, and in the Expiring lots report.

To turn lot tracking on or off for the whole workspace, an admin uses **Settings → Workspace → Lot control**.

## See history

> _Screenshot: the History tab on a part._

Open a part and click **History**. You see every stock event:

- Date
- Operation (add, remove, move-out, move-in, consume, receive)
- Quantity change (Δ)
- Storage involved
- Comments

For a workspace-wide view, open **Parts → Stock history** at the top of the Parts list. That shows the most recent 500 events across every part.

History rows are never deleted. If you removed something by mistake, add it back — both events stay in the record.

## Found bag inline

If you walk up to the bench, scan a bag, and need to log "I just took five of these", you don't need to open the part. The [scan-to-import](scan-import.md) page recognises a re-scanned bag and shows a Quick remove field on the row. Type the quantity, click the **−** button, and the stock comes off — done.

## What to do if it doesn't work

- **Remove says "Quantity must be between 1 and N"** — you typed more than the source bag holds. Reduce the number, or pick a different source bag.
- **Add fails with a serialized error** — the part is marked **Serialized** and your workspace requires a serial number. Fill in **Serial number** and set quantity to 1.
- **You can't pick a destination for Move** — the destination is full or archived. Pick a different bin, or unmark it as full in **Storage → Settings**.
- **History is missing a row** — invalidation lag; reload the page.
