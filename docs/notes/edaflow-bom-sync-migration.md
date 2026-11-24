# Switching the filter BOM push to `bom/sync`

Audience: engineer

A note for the owner of `NeuronSW2/filter/bom/stockmanager_sync.py`
(and any other per-project push script): what to change now that
stockManager can update a BOM in place. This repo does not edit that
script.

## Why

`cmd_bom` in that script deletes every entry of the project
(`DELETE /api/projects/{id}/entries/{entry_id}` in a loop) and re-imports
the CSV through `bom/preview` + `bom/import`. Deleting an entry cascades
through `build_stage_lines` (`backend/app/domain/builds/models.py:116-117`,
`ON DELETE CASCADE`) and the purchase-plan lines, so every push loses build
history, manual part matches, attrition and comments. A push that fails
half-way also leaves the project empty or doubled.

`POST /api/projects/{id}/bom/sync` replaces both steps
([API reference](../api/projects.md#post-apiprojectsproject_idbomsync)):
unchanged lines are not written, changed lines keep their id, lines no
longer in the BOM are archived (and come back with their id if they
reappear), and `dry_run: true` shows the diff first.

## What to change in `cmd_bom`

1. Keep the project lookup/creation.
2. Drop the delete loop and the `bom/preview` + `bom/import` calls.
3. Build `rows` from `stockmanager_bom.csv` (`mpn,quantity,designators,value,footprint`):

   ```python
   rows = [{"mpn": r["mpn"], "quantity": int(r["quantity"]),
            "designators": r["designators"],   # "C1,C2,C10,C11" is split server-side
            "value": r["value"], "footprint": r["footprint"]}
           for r in csv.DictReader(open(os.path.join(HERE, "stockmanager_bom.csv")))]
   ```

4. Call it twice — once to look, once to write:

   ```python
   st, j = api("POST", f"/api/projects/{proj['id']}/bom/sync", {"rows": rows, "dry_run": True})
   print("diff", {k: j["data"][k] for k in ("added", "updated", "removed", "unchanged", "unmatched")})
   st, j = api("POST", f"/api/projects/{proj['id']}/bom/sync", {"rows": rows})
   ```

Notes:

- The CSV uses a KiCad reference range (`C6-C8`). The server expands
  same-prefix ranges, so `C3,C4,C6-C8` and `C3,C4,C6,C7,C8` are the same
  line; nothing to do.
- The MPN alone matches a part; a differently spelled manufacturer (or
  none) does not stop it (`bom_import.py::_match_part`).
- Better still, export the `StockManager` field and send it as `id_code`
  (`https://parts.matescb.cz/parts/<uuid>`): it is matched before the MPN
  and survives an MPN correction.

## From `netsch parts sync`

`netsch/stock/sync.py::export_bom` already produces the right shape. Its
`refs` is a one-element list holding kicad-cli's comma-joined references
(it splits on whitespace, kicad-cli joins with `,`); send it as is, the
server splits it. Each group maps to one row:

| netsch key | sync row field |
|---|---|
| `refs` | `designators` |
| `qty` | `quantity` |
| `value` | `value` |
| `footprint` | `footprint` |
| `mpn` | `mpn` |
| `manufacturer` | `manufacturer` |
| `part_url` | `id_code` |

`backend/tests/test_bom_sync.py::netsch_row_to_sync_row` is this mapping,
exercised against a fixture of the 24 V filter export.
