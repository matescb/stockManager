# Scan bag codes to bulk-import stock

Audience: end user

Use a barcode scanner (or your phone camera) to add many parts at once from supplier-printed bag labels.

Most parts ship in plastic bags with a 2D barcode that encodes the manufacturer part number, quantity, lot, and serial. The scan-import flow reads the barcode, looks up the part, and queues a stock-add for each new bag.

## Set up scanning

1. Open the **Parts** list.
2. Click **Scan** (top right).

> _Screenshot: the Scan to import page with the camera viewport on the left and the queue on the right._

The page has two halves:

- **Left** — the camera viewport and a status line.
- **Right** — the storage picker, a Submit button, and the queue of bags scanned so far.

The browser asks for camera permission the first time. Allow it. If you have a USB barcode reader configured to type into the browser, it works the same way — focus the page and scan.

Your administrator may have configured a higher-quality scanner engine. If so, scanning works without further setup. The default engine (zxing) handles the common 2D codes printed by Mouser and DigiKey.

## Pick where new stock goes

Before scanning many bags, set the **Storage location** dropdown on the right. Every queued bag lands there when you submit. You can override it per-bag later, or leave it blank and pick storage at receive time.

A faster shortcut: open a storage location and click **Scan into here** at the top. That opens the scan page with the destination already set.

## Scan bags

1. Hold a bag in front of the camera. The scanner beeps (visually) when it reads the code.
2. A new row appears in the queue. The status shows what happened:
   - **Found** — the part exists in your workspace; ready to import. Optionally edit the **Quantity** if the bag's printed quantity is wrong.
   - **Looking up…** — the app is checking the supplier for an unknown MPN. Wait a moment.
   - **New part** — looked up successfully and ready to create + add stock.
   - **Duplicate** — that exact bag (same signature) is already in your queue or already in stock. Click **Open existing** to jump to it instead.
   - **Found bag (re-scan)** — you've scanned this exact bag before and it's in stock. The "Quick remove" mini-form appears: enter how many you used and click the button to subtract from this bag.
   - **Failed** — the supplier lookup failed. Re-scan to retry, or remove the row.

> _Screenshot: the queue with one Found row, one Found bag (re-scan) row, and one Duplicate row._

3. Repeat for each bag. The list scrolls.

To remove a row from the queue, click the trash icon on it. The session is saved in your browser, so reloading the page (or closing the tab and coming back) restores the queue.

## Import the queue

1. Double-check the storage destination on the right.
2. Click **Submit** (or **Import N items**).
3. The page summarises how many parts were created and how many failed.

Successful rows disappear from the queue. Failed rows stay so you can fix them.

## Re-scan a known bag

If you scan a bag that's already on the shelf, the row says **Found bag** and shows the current quantity, lot, and storage. To take parts out of it:

1. Type the quantity used into the **Quick remove** box on that row.
2. Click the **−** button.

This calls **Remove stock** on the part with that lot and storage prefilled — it's a shortcut for when you walk up to the bench, scan the bag, and need to log "I took five of these".

## What to do if it doesn't work

- **Camera doesn't open** — the browser blocked permission, or another app is using the camera. Click the lock icon in the address bar to grant permission, then reload.
- **Scanner reads the code but the row says "Failed"** — the supplier lookup timed out. Re-scan the bag to retry; the app retries automatically up to three times for transient errors.
- **The scanner won't read the code at all** — clean the bag, hold steady at 10–20 cm, ensure the room has light. If your administrator paid for the higher-quality engine, ask them to set the workspace's scanner license key in **Settings → Workspace**.
- **Submit says "Nothing to import"** — every row is in **Duplicate** or **Failed** state. Remove the rows, re-scan, or open the existing parts directly.
