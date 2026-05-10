# Manage parts

Audience: end user

Create, edit, archive a part. Pick the right type. Use supplier lookup.

A **part** is a thing you buy or build — a resistor, a connector, a finished sub-board. Stock and orders attach to parts. Projects' bills of materials reference parts.

## Pick a part type

You choose the type when you create the part. You cannot change it later.

- **Linked (MPN)** — has a real manufacturer part number and your workspace can look it up on a supplier (Mouser, DigiKey). Most parts are linked. The app pulls description, image, datasheet, specs, and price/stock data from the supplier.
- **Local** — a part you keep in stock but don't pull from a supplier. Custom-made hardware, in-house labels, anything without a manufacturer part number. You fill in everything by hand.
- **Meta-part** — a placeholder used in a bill of materials. Real parts (its **members**) can satisfy a meta-part. Useful for "any 10k 0402 1% resistor". Meta-parts don't hold stock themselves; their members do.
- **Sub-assembly** — something you build from other parts. Has its own bill of materials via a project; ends up in stock when a build completes.

If unsure, pick **Linked** when the part has an MPN and your workspace has a provider configured. Pick **Local** otherwise.

## Create a part

> _Screenshot: the Create part form with the MPN Lookup button visible._

1. Click **Parts** in the sidebar.
2. Click **+ Part**.
3. Pick a **Type**.
4. Enter the **MPN** (manufacturer part number). Required for Linked parts; optional for the others.
5. For Linked parts, click **Lookup** next to the MPN field. The form fills in manufacturer, description, footprint, datasheet, and an image preview. Re-click to lookup again with a different MPN.
6. **Name** defaults to the MPN. Override only if you want a friendlier display name.
7. Fill in **Manufacturer**, **Internal part number**, **Description**, **Footprint** as needed.
8. Optionally pick a **Default storage location**. New stock added to this part lands there if you don't override.
9. Tick **Serialized** only if every unit has its own serial number (rare; mostly for finished assemblies).
10. Click **Create**.

If the MPN is already used by an active part in this workspace, you get a yellow warning with a link to the existing part. Open that one instead — duplicates are not allowed.

## Edit a part

> _Screenshot: the Part info tab with the tabs row at the top._

Open a part. The tabs along the top group what you can do:

- **Part info** — read-only summary of what's set.
- **Specs** — supplier specs and your own custom fields.
- **Sourcing** — visible only on Linked parts. Pricing, stock, lead time pulled from the supplier.
- **Stock** — what's on hand and where.
- **Add / Remove / Move stock** — see [stock](stock.md).
- **History** — every change to this part's stock.
- **Lots** — production lots if you use lot tracking.
- **Substitutes** — other parts that can replace this one.
- **Members** — visible on Meta-parts. The real parts that satisfy this meta-part.
- **Attachments** — datasheets, photos, notes.
- **Activity** — non-stock events (refresh, edits).
- **Settings** — low-stock threshold, attrition, default storage, serialized, published.
- **Other** — archive / restore.

Editable text fields use **Settings**. Custom field values use **Specs**. Identity fields (name, MPN, manufacturer, footprint) — TODO(verify-ui): there's no edit form on Part info; check whether identity edits live in a separate "Edit part" route or only via supplier refresh and attachments.

The **Sourcing** tab can show TrustedParts lifecycle, supply-chain, and tariff badges above the distributor table when TrustedParts returns them. Distributor rows can include RoHS region pills, availability text next to stock, and a quantity hint when TrustedParts requires buying in multiples; custom quantities are rounded up to that multiple when you leave the field.

### Refresh from supplier

Linked parts show a banner with the supplier name and "last refreshed". Click **Refresh** to re-pull data. Provider-sourced fields are re-written; values you marked **Locally edited** are kept.

### Set a low-stock threshold

In **Settings**, enter a number in **Low-stock report quantity**. Parts that drop below this number show up in the Low-stock report.

## Archive a part

Archiving hides a part from lists but preserves its history and stock.

1. Open the part.
2. Click the **Other** tab.
3. Click **Archive part** and confirm.

To bring it back, open the **Archived** view from the Parts list, click the part, then **Other** → **Restore from archive**.

You can also archive several at once: tick the rows on the Parts list and click **Delete (N)** in the bar that appears.

## What to do if it doesn't work

- **Lookup says "Not found" or the button is missing** — your workspace's provider isn't set, or the MPN really doesn't exist upstream. Ask an admin to configure Mouser or DigiKey in **Settings → Workspace → Parts provider**.
- **"MPN is already used by part …"** — that exact MPN is already on an active part. Open the existing one instead, or archive the old one if it's a stale duplicate.
- **The Refresh banner says "stale"** — last successful refresh was over 30 days ago. Click **Refresh**.
- **You can't change a Linked part's manufacturer or MPN** — Linked parts are pinned to their supplier match. Archive and create a new part if you really need to swap MPN.
