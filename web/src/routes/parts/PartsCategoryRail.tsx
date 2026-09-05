/**
 * The category filter for `/parts`, in two shapes.
 *
 * `PartsCategoryRail` is a tree beside the table at `lg` and up.
 * `PartsCategoryBar` sits above the table and carries the same filter as a
 * native `<select>` of path-style names, shown only below `lg` — a rail
 * plus the table is one column too many once the 240px sidebar is
 * accounted for. It also owns the two notices that explain a deep link
 * pointing at a category that is archived or gone; hiding the control
 * entirely on narrow screens would leave such a link with no way out.
 *
 * Both are in this file rather than in `PartsList` so the list keeps its
 * shape: it reads two search params and renders two elements.
 */
import { useMemo } from "react";
import { Link } from "react-router-dom";

import CategoryTree from "@/components/CategoryTree";
import { categoryPath } from "@/lib/categoryTree";
import type { PartCategory } from "@/lib/schemas";

type SharedProps = {
  categories: readonly PartCategory[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  includeDescendants: boolean;
  onIncludeDescendantsChange: (next: boolean) => void;
};

/** Archived categories are fetched (a part can still point at one, and the
 * Category column would otherwise render blank) but are not offered as a
 * filter — they are hidden from every other picker in the app. The one
 * exception is a category the URL already names; see `railRows`. */
function useActive(categories: readonly PartCategory[]) {
  return useMemo(
    () => categories.filter((c) => c.archived_at === null),
    [categories],
  );
}

function SubcategoryToggle({
  includeDescendants,
  onIncludeDescendantsChange,
}: Pick<SharedProps, "includeDescendants" | "onIncludeDescendantsChange">) {
  return (
    <label className="flex items-center gap-2 text-xs text-muted cursor-pointer">
      <input
        type="checkbox"
        checked={includeDescendants}
        onChange={(e) => onIncludeDescendantsChange(e.target.checked)}
      />
      Include subcategories
    </label>
  );
}

export default function PartsCategoryRail({
  categories,
  selectedId,
  onSelect,
  includeDescendants,
  onIncludeDescendantsChange,
}: SharedProps) {
  const active = useActive(categories);
  const selected = selectedId
    ? categories.find((c) => c.id === selectedId) ?? null
    : null;

  // An archived category still filters server-side (parts pointing at it
  // were not unfiled), so if the URL names one, show it in the tree rather
  // than rendering a rail with nothing highlighted.
  const railRows = useMemo(
    () =>
      selected !== null && selected.archived_at !== null
        ? [...active, selected]
        : active,
    [active, selected],
  );

  return (
    // `sticky` sits on the flex item itself with `self-start`, not on an
    // inner div: with `items-start` on the row the aside is only as tall as
    // its content, so a sticky child would have no room to travel. The tree
    // scrolls inside its own box rather than growing the page.
    <aside className="hidden lg:block w-56 shrink-0 sticky top-4 self-start">
      <div className="card p-2 max-h-[calc(100vh-6rem)] overflow-y-auto">
        <div className="flex items-center justify-between px-1 pb-2">
          <span className="section-title">Categories</span>
          <Link
            to="/settings/categories"
            className="text-xs text-muted hover:text-text underline underline-offset-2"
          >
            Manage
          </Link>
        </div>
        <CategoryTree
          rows={railRows}
          selectedId={selectedId}
          onSelect={onSelect}
          treeId="parts"
        />
        {selectedId !== null && (
          <div className="border-t border-border mt-2 pt-2 px-1">
            <SubcategoryToggle
              includeDescendants={includeDescendants}
              onIncludeDescendantsChange={onIncludeDescendantsChange}
            />
          </div>
        )}
      </div>
    </aside>
  );
}

export function PartsCategoryBar({
  categories,
  selectedId,
  onSelect,
  includeDescendants,
  onIncludeDescendantsChange,
}: SharedProps) {
  const active = useActive(categories);
  const selected = selectedId
    ? categories.find((c) => c.id === selectedId) ?? null
    : null;

  const options = useMemo(
    () =>
      active
        .map((c) => ({ id: c.id, label: categoryPath(active, c.id) }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [active],
  );

  return (
    <>
      <div className="lg:hidden flex flex-wrap items-center gap-3 mb-3">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted">Category</span>
          <select
            className="input w-auto"
            value={selectedId ?? ""}
            onChange={(e) => onSelect(e.target.value || null)}
          >
            <option value="">All parts</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
            {selected !== null && selected.archived_at !== null && (
              <option value={selected.id}>{selected.name} (archived)</option>
            )}
          </select>
        </label>
        {selectedId !== null && (
          <SubcategoryToggle
            includeDescendants={includeDescendants}
            onIncludeDescendantsChange={onIncludeDescendantsChange}
          />
        )}
      </div>

      {/* A deep link can name a category that has since been deleted. The
          API answers 404, so without this the user gets a generic error
          banner and no way back. */}
      {selectedId !== null && selected === null && (
        <div className="card p-3 mb-3 text-sm text-muted">
          That category no longer exists.{" "}
          <button type="button" className="underline" onClick={() => onSelect(null)}>
            Show all parts
          </button>
        </div>
      )}
      {selected !== null && selected.archived_at !== null && (
        <div className="card p-3 mb-3 text-sm text-muted">
          <span className="pill mr-2">Archived</span>
          Showing parts still filed under &ldquo;{selected.name}&rdquo;. The
          category is archived, so it is no longer offered when filing new
          parts.
        </div>
      )}
    </>
  );
}
