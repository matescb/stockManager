# Storage locations

Audience: end user

Shelves, bins, reels, drawers — anywhere you put parts. Set a default per part. Archive what you no longer use.

A **storage location** is a labelled place. The app doesn't care if it's a literal shelf, a drawer, a reel rack, or a numbered bin — it's just a name you can attach stock to.

## See your storage

Click **Storage** in the sidebar. You see a table of every location with:

- **Name**
- **Description**
- **Single-part** — yes if the bin only holds one part type.
- **Full** — yes if you've marked the bin as no longer accepting new stock.

Click a row to open the location.

## Add a storage location

> _Screenshot: the Create storage location form._

1. Click **Storage** in the sidebar.
2. Click **+ Storage** (top right).
3. Enter a **Name** (e.g. "Shelf A1", "Reel rack 3").
4. Optionally add a **Description**.
5. Optionally tick the flags:
   - **Limit to a single part** — only one part type may be stored here. Trying to add a second part to it fails.
   - **Only allow existing parts** — block creation of new parts via this bin's Scan-into-here flow. Useful for "this bin is for already-known stock only".
   - **Mark as full** — the location is hidden from receive/move destination pickers. Use this for bins you've physically filled up.
6. Click **Create**.

You can change all three flags later from the location's **Settings** tab.

## See what's in a location

Open a location and click the **Info** tab. The table lists every (part, lot) row currently held with its quantity. Empty bins show "Empty".

The **History** tab shows every stock event that touched this location — useful for "who put what here, and when".

> _Screenshot: the storage Info tab with the parts table and the "Scan into here" and "Audit" buttons at the top._

## Fill a box by scanning (Intake)

Use this to load an old box or a new delivery. Open the box and click **Scan into here** (or the **Intake** tab).

1. Scan each bag's 2D code with the camera, or with a USB / Bluetooth barcode reader (no need to click anything first; just scan).
2. Each bag appears in the list and is saved straight away into this box as its own bag (lot). The quantity comes from the label.
3. If the real count differs, type the right number in the row and click **Update count**.
4. No label? Type the **MPN** and **Qty** and click **Add by MPN**.

What each row tells you:

- **Added**: the bag is now in this box. **new part** means the part was not in your library and was created from your parts provider.
- **Already in this box**: this bag was scanned into this box before. Only its count changes if you edit it.
- **Recorded in another box**: the bag is on record somewhere else. Click **Move here** if you moved it, or **Add as new bag** if it is a second bag with an identical label.
- **Not in the library**: the provider has no match for the MPN. Use **Create part…** and then add it by MPN.

Untick **Save each scan immediately** if you would rather check the list first and then click **Save all**. **Undo** sets a just-added bag back to zero.

Scanning a box's printed label switches to that box.

## Check a box (Audit)

Use this to make the records match what is really in the box. Open the box and click **Audit**.

1. Scan every bag in the box. Each scanned bag moves to **Scanned**, showing its recorded quantity. Change the number if your count differs.
2. Bags still listed under **Not scanned** (in red) were not found. For each one, pick:
   - **Write off** (default): the record drops to zero. History is kept.
   - **Move to…**: it lives in another box. Pick which.
   - **Keep**: leave the record as it is.
   - Or click **It's here** if the bag is there but its label will not scan.
3. A scanned bag that is not on record for this box shows under **Found here, not recorded**. Click **Move here** or **Add to box**.
4. Click **Finish audit**. Everything is applied at once.

Stock entered before bag tracking existed shows as **old record, not tracked per bag** and is written off by default. Scan the actual bags to replace it.

Progress is kept if you reload the page. **Start over** clears it.

## Set a default location for a part

When most of a part lives in one bin, set that bin as the part's default. New stock then lands there unless you override.

1. Open the part.
2. Click the **Settings** tab.
3. Pick the bin in **Default storage location**.
4. Tick **Default location is mandatory** if you want to disallow overriding (the storage dropdown becomes a one-option fixture).
5. Click **Save**.

## Archive a location

Archiving hides the location from lists but keeps it visible in old history rows.

1. Open the location.
2. Click the **Other** tab.
3. Click **Archive** and confirm.

To bring it back, open the **Archived** tab on the Storage list, click the row, then **Other → Restore**.

You cannot archive a location that is set as a part's default storage. Either change the part's default first, or archive the part as well.

## What to do if it doesn't work

- **The receive form's Storage dropdown doesn't list a bin you expect** — it's archived, or marked **Mark as full**. Open the location's Settings tab and untick **Mark as full**, or restore from archive.
- **Adding a part to a single-part bin fails** — the bin already has a different part. Move the existing stock out, or pick another bin.
- **You can't archive a bin** — a part has it set as its default mandatory storage. Open the part's Settings tab and clear **Default storage location**, or untick **mandatory**.
