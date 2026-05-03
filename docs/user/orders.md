# Purchase orders and receiving

Audience: end user

Record what you've ordered from a supplier. Mark it as received when it shows up — that adds the stock automatically.

A **purchase order** (or "order") is a list of parts you've ordered from one supplier. The order moves through statuses as you log work against it: **draft → open → partial → received** (or **cancelled**).

## See your orders

Click **Orders** in the sidebar. You see every order with its supplier, status, and a "received / ordered" progress count.

Click an order to open it. Click **Archived** to see archived orders. Click **+ Order** to create a new one.

## Create a purchase order

> _Screenshot: the New order form._

1. Click **Orders** in the sidebar.
2. Click **+ Order**.
3. Enter a **Name** — your reference for the order (e.g. "PO-2026-001" or "Mouser May 2026"). Required.
4. Optionally fill **Supplier**, **Currency** (3-letter code), **Ordered on**, **Expected on**, **Comments**.
5. Click **Create order**.

You land on the order's detail page with no lines yet.

## Add lines

> _Screenshot: the order detail with the inline + Line form open._

1. Open the order.
2. Click **+ Line** (top right of the Lines card).
3. In **Part**, pick a part from the dropdown. Or leave it as "— free text —" and type a name in **Free-text name** for parts you haven't created yet (you can match them to a real part later).
4. Set **Qty** (defaults to 1).
5. Optionally set **Unit price**.
6. Click **Add**.

Repeat for each line. To remove a line that hasn't been received yet, click **Delete** on its row.

## Mark received

Once the parcel arrives, log what landed.

> _Screenshot: the Receive panel with quantity inputs per line and a Storage dropdown._

1. Open the order. The **Receive** card appears below the lines if any line has parts outstanding.
2. For each line you got something for:
   - Type how many came in into the **Receive** column. The max is shown as **Outstanding**.
   - Pick a **Storage** destination (optional but recommended).
   - For serialized parts, type the **Serial #** (required for those).
3. Optionally set **Received on** (defaults to today).
4. Click **Receive**.

Stock is added immediately to each part you received. The order's **Received** total goes up. If the order is now fully received, its status flips to **received** and the Receive card disappears.

You can receive a partial shipment — type quantities only for what arrived, leave the rest at zero, and click **Receive**. The next shipment uses the same form for the remainder.

## See what landed

After receiving, open any received part. Its **Stock** tab shows the new quantity in the bin you picked. Its **History** tab shows a row with operation `receive` referencing the order.

The order's **Activity** panel (bottom of the order page) lists every event: line added, line removed, partial receive, full receive, archive.

## Archive an order

Click **Archive** at the top right of the order. Archived orders disappear from the main list and move to the **Archived** tab. To restore, open the archived order and click **Restore**.

## What to do if it doesn't work

- **The Receive card doesn't appear** — every line is either fully received, has no part attached (free-text only), or the order is closed/cancelled/archived. Match free-text lines to a real part by editing the line.
- **"Enter a quantity on at least one row"** — you clicked Receive without typing any numbers. Fill in at least one Receive cell.
- **A serialized part won't receive** — you must type a serial number for it. Each unit needs its own serial; some workflows split a single line into multiple receives.
- **The Storage dropdown is missing a bin** — the bin is archived or marked full. Pick another, or open **Storage → Settings** and untick **Mark as full**.
