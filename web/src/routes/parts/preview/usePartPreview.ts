import { useCallback, useMemo } from "react";
import type { KeyboardEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useIsXlViewport } from "@/lib/useMediaQuery";
import type { Part } from "@/types";

/**
 * Selection state for the parts-list preview pane.
 *
 * **Selection lives in the URL**, as `/parts?sel=<part id>`. Component
 * state would have been cheaper, but a selected row then stops being
 * linkable, and deep-linking is a first-class concern here — `App.tsx`
 * goes out of its way to carry `pathname` + `search` + `hash` across the
 * login round-trip (issue #304). Putting it in a search param also buys
 * back/forward for free and keeps the full `/parts/:partId/*` routes
 * completely untouched.
 *
 * **Below `xl` a row click navigates to the full page, exactly as it did
 * before this pane existed.** There is no room for a split pane on a
 * phone, and a drawer would be a second thing to build, learn and get
 * wrong. Narrow viewports are byte-for-byte unchanged.
 *
 * **History: push on activation, replace on browse.** Clicking a row (or
 * pressing Enter on it) is a deliberate act, so it pushes an entry and
 * Back closes the preview. Arrow-keying down the list is browsing, so it
 * replaces — twenty arrow presses must not cost twenty Back presses to
 * undo.
 */

/** The search param that carries the selected part id. */
export const PREVIEW_PARAM = "sel";

export type PartPreview = {
  /** Selected part id, or null when nothing is selected (or below `xl`). */
  selectedId: string | null;
  /**
   * The already-loaded list row for `selectedId`, when the list happens
   * to hold it. This is what lets the pane paint before any fetch —
   * `/parts` rows are full part objects, not a projection.
   */
  selectedRow: Part | null;
  /** `true` at `xl` and wider, i.e. when a pane can be shown at all. */
  isXlViewport: boolean;
  /** Row click / Enter / Space. Selects at `xl`+, navigates below it. */
  openRow: (row: Part) => void;
  /** Arrow-key focus move. Follows the focused row into the pane. */
  previewRow: (row: Part) => void;
  /** Clear the selection (the pane's close button, and Escape). */
  closePreview: () => void;
  /** Extra class for the row currently shown in the pane. */
  rowClassName: (row: Part) => string | undefined;
  /**
   * Text for an always-mounted `aria-live="polite"` region on the list.
   * The pane is a landmark, not a dialog, so nothing moves focus into it
   * and a screen-reader user arrow-keying down the rows would otherwise
   * get no signal that the preview beside them changed. The region has to
   * live *outside* the pane — a live region that mounts at the same
   * moment its content appears is not reliably announced.
   */
  announcement: string;
  /**
   * Escape handler for the element wrapping the table *and* the pane.
   * Deliberately scoped rather than bound to `window`: `Modal.tsx` closes
   * on a window-level Escape without calling `preventDefault`, so a global
   * listener here would dismiss a dialog and the preview with one press.
   */
  onContainerKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
};

export function usePartPreview(rows: Part[]): PartPreview {
  const nav = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const isXlViewport = useIsXlViewport();

  // A `?sel=` that arrives on a narrow viewport is kept in the URL but
  // ignored: no pane is rendered and no navigation is hijacked. Widening
  // the window then reveals the preview the link was pointing at.
  const paramValue = searchParams.get(PREVIEW_PARAM);
  const selectedId = isXlViewport ? paramValue : null;

  const selectedRow = useMemo(
    () => (selectedId ? rows.find((r) => r.id === selectedId) ?? null : null),
    [rows, selectedId],
  );

  const setSelection = useCallback(
    (id: string | null, history: "push" | "replace") => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (id) next.set(PREVIEW_PARAM, id);
          else next.delete(PREVIEW_PARAM);
          return next;
        },
        { replace: history === "replace" },
      );
    },
    [setSearchParams],
  );

  const openRow = useCallback(
    (row: Part) => {
      if (!isXlViewport) {
        nav(`/parts/${row.id}/info`);
        return;
      }
      setSelection(row.id, "push");
    },
    [isXlViewport, nav, setSelection],
  );

  const previewRow = useCallback(
    (row: Part) => {
      // Below `xl` the arrow keys keep doing what they always did — move
      // focus, and nothing else.
      if (!isXlViewport) return;
      setSelection(row.id, "replace");
    },
    [isXlViewport, setSelection],
  );

  const closePreview = useCallback(() => setSelection(null, "replace"), [setSelection]);

  const rowClassName = useCallback(
    (row: Part) => (row.id === selectedId ? "bg-accent/10" : undefined),
    [selectedId],
  );

  const onContainerKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (event.key !== "Escape" || !selectedId) return;
      event.stopPropagation();
      closePreview();
    },
    [closePreview, selectedId],
  );

  const announcement = selectedId
    ? `Previewing ${selectedRow?.name ?? "the selected part"}`
    : "";

  return {
    selectedId,
    selectedRow,
    isXlViewport,
    openRow,
    previewRow,
    closePreview,
    rowClassName,
    announcement,
    onContainerKeyDown,
  };
}
