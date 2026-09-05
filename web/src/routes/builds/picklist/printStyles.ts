/**
 * The pick list's print stylesheet, injected as a `<style>` element by
 * `PickListSheet` rather than added to `src/index.css`.
 *
 * Two reasons it lives here and not in the global stylesheet:
 *
 * * the rules only exist while the sheet is mounted, so they can never
 *   change how any other page prints;
 * * `index.css` is a high-traffic shared file and this is a self-contained
 *   feature.
 *
 * Chrome is hidden with the `visibility` trick rather than by naming
 * AppShell's `<header>` / `<aside>`: it hides whatever the shell happens
 * to render today without this file having to track it, and it survives a
 * layout refactor. `PICKLIST_ROOT_ATTR` is the anchor.
 */

/** Data attribute marking the printable region. */
export const PICKLIST_ROOT_ATTR = "data-picklist-root";

/** Class for on-screen controls that must not reach the paper. */
export const NO_PRINT_CLASS = "picklist-noprint";

export const PICK_LIST_PRINT_CSS = `
[${PICKLIST_ROOT_ATTR}] .picklist-sheet { max-width: 60rem; }

@page {
  size: A4 portrait;
  margin: 14mm;
}

@media print {
  /* Hide the app shell without naming it: everything goes invisible,
     then the sheet and its subtree come back. */
  body * { visibility: hidden; }
  [${PICKLIST_ROOT_ATTR}], [${PICKLIST_ROOT_ATTR}] * { visibility: visible; }
  [${PICKLIST_ROOT_ATTR}] {
    position: absolute;
    left: 0;
    top: 0;
    width: 100%;
    margin: 0;
    padding: 0;
  }

  .${NO_PRINT_CLASS} { display: none !important; }

  /* Paper is white; the app's theme tokens are not. Force ink-on-paper so
     a dark-mode operator doesn't print a black rectangle. */
  [${PICKLIST_ROOT_ATTR}], [${PICKLIST_ROOT_ATTR}] * {
    background: transparent !important;
    color: #000 !important;
    box-shadow: none !important;
  }
  [${PICKLIST_ROOT_ATTR}] .picklist-sheet { max-width: none; }
  [${PICKLIST_ROOT_ATTR}] .picklist-short { font-weight: 700; }

  /* Pagination. A stop is one shelf: splitting it across a page break
     sends the operator back for a second visit, which is the one thing
     this sheet exists to prevent. Keep each stop whole when it fits;
     repeat the table header when it genuinely cannot. */
  [${PICKLIST_ROOT_ATTR}] .picklist-stop { break-inside: avoid; page-break-inside: avoid; }
  [${PICKLIST_ROOT_ATTR}] tr { break-inside: avoid; page-break-inside: avoid; }
  [${PICKLIST_ROOT_ATTR}] thead { display: table-header-group; }
  [${PICKLIST_ROOT_ATTR}] tfoot { display: table-footer-group; }
  [${PICKLIST_ROOT_ATTR}] h2 { break-after: avoid; page-break-after: avoid; }
  [${PICKLIST_ROOT_ATTR}] .picklist-section { break-before: auto; }
  [${PICKLIST_ROOT_ATTR}] .picklist-summary { break-before: page; page-break-before: always; }

  [${PICKLIST_ROOT_ATTR}] table {
    width: 100%;
    border-collapse: collapse;
    font-size: 10pt;
  }
  [${PICKLIST_ROOT_ATTR}] th,
  [${PICKLIST_ROOT_ATTR}] td {
    border-bottom: 1px solid #999 !important;
    padding: 4px 6px;
    text-align: left;
  }
  [${PICKLIST_ROOT_ATTR}] .picklist-box {
    border: 1px solid #000 !important;
    display: inline-block;
    width: 10px;
    height: 10px;
  }
}
`;
