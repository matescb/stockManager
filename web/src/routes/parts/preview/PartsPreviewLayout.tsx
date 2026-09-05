import type { ReactNode } from "react";
import PartPreviewPane from "./PartPreviewPane";
import type { PartPreview } from "./usePartPreview";

/**
 * Split-pane frame for the parts list: table on the left, preview on the
 * right at `lg` and wider.
 *
 * This exists so `PartsList.tsx` changes by one tag rather than gaining a
 * level of JSX nesting — the category tree (#909) landed on the same
 * route, and a re-indented 120-line block would have been a rebase
 * conflict for no benefit.
 *
 * **How it composes with the category rail.** #909 put the rail in an
 * outer flex row (`PartsCategoryRail` + a `flex-1 min-w-0` column) rather
 * than in this one, so the two nest: the outer row splits rail | column,
 * and this row splits that column into table | preview. The rendered
 * result is the intended three columns, and neither component had to
 * know about the other.
 *
 * The widths are why the preview waits for `xl` while the rail appears at
 * `lg` — see `XL_VIEWPORT_QUERY`. At `lg` the rail and table already own
 * the row; a pane there would leave the table 176px.
 *
 * `min-w-0` on the table column is load-bearing: a flex item defaults to
 * `min-width: auto`, so without it the table refuses to shrink below its
 * content width and pushes the preview off screen instead of scrolling
 * inside its own `overflow-auto` (which `DataTable` already has).
 */
export default function PartsPreviewLayout({
  preview,
  children,
}: {
  preview: PartPreview;
  children: ReactNode;
}) {
  return (
    // Escape is bound here rather than on `window`: `Modal.tsx` closes on
    // a window-level Escape without calling `preventDefault`, so a global
    // listener would dismiss an open dialog and the preview at once.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div className="flex items-start gap-4" onKeyDown={preview.onContainerKeyDown}>
      <p className="sr-only" role="status" aria-live="polite">
        {preview.announcement}
      </p>
      <div className="min-w-0 flex-1">{children}</div>
      {preview.selectedId && (
        <PartPreviewPane
          key={preview.selectedId}
          partId={preview.selectedId}
          fallbackRow={preview.selectedRow}
          onClose={preview.closePreview}
        />
      )}
    </div>
  );
}
