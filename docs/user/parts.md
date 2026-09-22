# Manage parts

Audience: end user

Create, edit, archive a part. Pick the right type. Name it the way the catalogue does. Use supplier lookup.

A **part** is a thing you buy or build — a resistor, a connector, a finished sub-board. Stock and orders attach to parts. Projects' bills of materials reference parts.

## Pick a part type

You choose the type when you create the part. The four types:

- **Linked (MPN)** — has a real manufacturer part number and your workspace can look it up on a supplier (Mouser, DigiKey). Most parts are linked. The app pulls description, image, datasheet, specs, and price/stock data from the supplier.
- **Local** — a part you keep in stock but don't pull from a supplier. Custom-made hardware, in-house labels, anything without a manufacturer part number. You fill in everything by hand.
- **Meta-part** — a placeholder used in a bill of materials. Real parts (its **members**) can satisfy a meta-part. Useful for "any 10k 0402 1% resistor". Meta-parts don't hold stock themselves; their members do.
- **Sub-assembly** — something you build from other parts. Has its own bill of materials via a project; ends up in stock when a build completes.

If unsure, pick **Linked** when the part has an MPN and your workspace has a provider configured. Pick **Local** otherwise.

### Linked and local follow the supplier

You don't have to keep these two straight yourself — the app does it for you:

- A local part becomes **Linked** the first time a supplier lookup succeeds for it. This is what happens when you refresh a part that was created by hand or pulled in from an imported bill of materials.
- A linked part becomes **Local** when you unlink it from its supplier. Its manufacturer, MPN and specs become yours to edit.

Meta-parts and sub-assemblies are never changed this way. Those say what a part *is*, not where its data comes from, so a meta-part that you refresh from a supplier stays a meta-part.

Where the type is shown — the part header, the parts list, the list preview — a linked part names its supplier next to the type, for example **linked · DigiKey**.

## How parts are named

A part's **name** says what the component *is*, so that the same component is recognisable wherever it turns up — a bill of materials, a pick list, the KiCad chooser.

- **Resistors, capacitors, inductors and the like** are named by their value, with the class letter in front: **R 10 kΩ 1% 0603**, **C 1 µF 50 V X7R 0805**, **C 1000 µF 50 V elyt**, **L 22 µH 5.3 A 1210**. The app builds that from the specs on the part, using the template set on its category.
- **Everything else** is named by its **manufacturer part number**: **STM32F103C8T6**. The manufacturer has its own field and the supplier's description has its own field, so neither needs to be in the name.
- **What a part does in one project** — "Servo integrator + bias monitor", "TL431 ref feed" — is not part of the name. Put it on the bill-of-materials line, which has a name and a comments field of its own.

The app names new parts this way for you. A supplier import is named by its MPN, and by its value once the part has a category and the specs to fill the template. A part you create by hand keeps whatever name you type.

If the specs needed for the value are missing, the part keeps its MPN as its name. Fill the specs in on the **Specs** tab and ask an admin to re-run the rename sweep.

> Two parts are allowed to end up with the same name. Nothing in the app identifies a part by its name — if you see a duplicate, it means you have two catalogue entries for one component, which is worth tidying up.

### Renaming what is already there

An admin can bring an existing catalogue up to this convention in one pass. It runs as a report first, so you can see every proposed change before anything is renamed.

Names you typed yourself are **not** renamed. The sweep only fixes names that came from an import — a supplier description, or a bill-of-materials column — and lists your hand-typed ones without touching them, so somebody can decide case by case. Anything it does replace is kept on the part as a spec called **alias**, so it stays searchable. If a part already has an **alias**, the sweep leaves that part alone rather than overwrite it.

Ask an admin for the report; it is a CSV listing every part, its old name, its proposed one, and where the old name ended up.

One thing to watch after a rename: if you import a bill of materials that identifies parts only by name, the renamed parts will no longer match and you may get duplicates. Import by manufacturer part number where you can, and check the parts list for duplicates after the first import following a rename.

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

## Show specs as columns

Pick a category on the Parts list, open the table's **Columns** menu, and
you'll find a **Specs** section under the usual column list: tick a spec —
resistance, tolerance, package — and it becomes a column you can sort by.
The choice is saved on the category, so everyone in your workspace sees the
same columns next time they open it, and a subcategory picks up whatever its
parent uses unless you give it its own. The columns above the Specs section
are the ordinary part fields, and hiding one of those only affects your own
browser.

Picking a top-level category like **Capacitors** offers every kind of
capacitor's specs at once, because the parts filed under it are a mix.
Specs that only apply to some of them are marked with the kinds they
belong to — **Dielectric** *ceramic*, **ESR** *electrolytic* — so you can
tell what a blank cell means. Pick the subcategory instead and you get just
that kind's specs.

Clicking a spec column heading sorts the whole list by that spec, not just
the rows on screen, and by value rather than by text — so 100 Ω comes
before 1 kΩ. Parts with no value for that spec go to the bottom either way.
Click **Save as default sort** if you want the list to open that way from
now on.

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
