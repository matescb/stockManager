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

> _Screenshot: the storage Info tab with the parts table and the "Scan into here" button at the top._

## Scan stock straight into a location

A shortcut: open the location and click **Scan into here** at the top. That opens the [scan-to-import](scan-import.md) page with this bin pre-selected as the destination, so you don't have to remember to set it.

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
