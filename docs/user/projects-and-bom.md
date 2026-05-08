# Projects and bill of materials

Audience: end user

A **project** is something you build. Its **bill of materials** (BOM) lists which parts and how many you need per unit. You import a BOM from a CSV file or add lines by hand.

## Create a project

> _Screenshot: the Create project form._

1. Click **Projects** in the sidebar.
2. Click **+ Project**.
3. Enter a **Name** and optional **Description**.
4. Click **Create**.

The project opens on the **Project info** tab. The tabs along the top are:

- **Project info** — name, description, last updated.
- **BOM** — the list of parts the project needs.
- **Import BOM** — upload a CSV/TSV.
- **Builds** — every build planned or completed against this project.
- **Other** — archive / restore.

## Import a BOM from CSV

> _Screenshot: the Import BOM step 1, file picker._

The fastest way to populate a BOM is to import the export your CAD tool produced.

1. Open the project and click the **Import BOM** tab.
2. Click the file picker and choose a CSV, TSV, or plain text file. Maximum size 4 MB.
3. The page parses the file and jumps to the mapping step.

> _Screenshot: the Import BOM step 2, column mapping with auto-detected separator and per-column dropdowns._

4. Check the detected **Separator**, **Encoding**, and **Has header** flags. Override if wrong, then click the reparse control.
5. For each column, pick what it represents:
   - **quantity** — how many of this part per unit.
   - **part** — the part's name (used to match by name if no MPN).
   - **mpn** — manufacturer part number (preferred matcher).
   - **manufacturer**, **internal_part_number**, **footprint** — extra match hints.
   - **designators** — schematic references like `R1, R2, R3`.
   - **comments** — free text per line.
   - **dnp** — "do not place"; line is recorded but not consumed at build time.
   - **id_code**, **cad_key** — uniquely identify a row across re-imports.
   - **ignore** — skip this column.
6. Set the **Designator separator** (usually a comma).
7. Click **Commit** (or **Import**).
8. The result page summarises:
   - **Inserted** — total lines added.
   - **Matched** — lines that found a real part in your workspace.
   - **Unmatched** — lines that couldn't be matched. They're imported as placeholders and need matching by hand on the BOM tab.

Click **Open BOM** to view the result.

### Auto-create missing parts

If your import screen or integration enables **Auto-create missing parts**, unmatched rows are turned into new parts during import instead of placeholder BOM lines.

The import still tries to match existing parts first. It only creates a part after it fails to match by internal ID, CAD key, internal part number, MPN, or part name.

Auto-created parts start with zero stock. They do not create stock entries or lots. The new part gets:

- **Name** from the BOM's part/name column, or the MPN if the name is blank.
- **MPN**, **Manufacturer**, and **Internal part number** from the mapped columns when present.
- **Provider** unset, so you can enrich it later.

Rows with neither an MPN nor a part/name are skipped when auto-create is enabled. They do not create a part or a BOM line.

### Save your mapping as a preset

If you import BOMs from the same CAD tool repeatedly, save the column mapping:

1. Set the mapping the way you want it.
2. Click the **Save preset** button (or similar — TODO(verify-ui): exact button label / location).
3. Name the preset (e.g. "KiCad export", "Mouser PCB BOM").

Next time you import, pick the preset to apply the same separator, encoding, and column mapping.

## Add or edit BOM lines by hand

Open the project and click the **BOM** tab.

> _Screenshot: the BOM table with one matched row and one unmatched row showing a Match dropdown._

Each row shows the quantity, the part (linked in green if matched), designators, comments, and a DNP flag. Unmatched rows have a red "unmatched" pill — click **Match…** on the row, pick a part from the dropdown, and click **Match**.

To remove a line, click **Delete** on its row.

TODO(verify-ui): adding a brand-new BOM line from the BOM tab — there's no "+ Line" button visible in the read-only BOM viewer; manual additions appear to require either editing the imported CSV and re-importing, or another route. Verify before relying on this.

## Use meta-parts for "any 10k 0402 1%"

If a row in your BOM doesn't need a specific manufacturer part — any 10k 0402 1% resistor will do — match it to a **meta-part** rather than a specific MPN. The build flow will then accept any of the meta-part's members at consume time. See [parts](parts.md#pick-a-part-type) for how meta-parts work.

## Archive a project

Open the project, click **Other**, click **Archive**. The project disappears from the active list. Restore from the **Archived** tab.

## What to do if it doesn't work

- **Import says "BOM file too large — max 4 MB"** — split the file or strip unused columns.
- **Lines come back as Unmatched** — the file's MPN or part name didn't match anything in your workspace. Either create the missing parts (via [scan-to-import](scan-import.md) or **+ Part**), then click **Match…** on each row, re-import after creating the parts, or enable auto-create where available.
- **Designators show up in one cell instead of split** — your **Designator separator** is wrong. Re-import with the right separator (often `,` or `;`).
- **Imported quantities are off** — your **Separator** or **Has header** detection was wrong. Re-import with the correct values.
