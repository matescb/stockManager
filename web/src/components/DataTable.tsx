import { ReactNode, useEffect, useMemo, useState } from "react";
import { Rows3, Rows4 } from "lucide-react";
import { cn } from "@/lib/cn";

export type Align = "left" | "right" | "center";

export type Column<T> = {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  accessor?: (row: T) => string | number | boolean | null | undefined;
  width?: string;
  hidden?: boolean;
  align?: Align;
};

type Density = "comfortable" | "compact";

type Props<T> = {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  searchPlaceholder?: string;
  initialSearch?: string;
  empty?: ReactNode;
  exportFilename?: string;
  /** Persists hidden columns + density to localStorage. */
  tableId?: string;
  /**
   * When true, renders a leftmost checkbox column and a header
   * "select-all" checkbox. Selection is uncontrolled (the table
   * tracks the set internally) but the parent gets notified via
   * `onSelectionChange`.
   */
  selectable?: boolean;
  /** Rendered right of the search box when at least one row is selected. */
  selectionAccessory?: (selectedIds: string[], clear: () => void) => ReactNode;
};

type Persisted = { hidden?: Record<string, boolean>; density?: Density };

function loadPersisted(tableId: string | undefined): Persisted {
  if (!tableId) return {};
  try {
    return JSON.parse(localStorage.getItem(`dt:${tableId}`) || "{}");
  } catch {
    return {};
  }
}

function savePersisted(tableId: string | undefined, p: Persisted) {
  if (!tableId) return;
  localStorage.setItem(`dt:${tableId}`, JSON.stringify(p));
}

function defaultAlignFor<T>(col: Column<T>, sample: T | undefined): Align {
  if (col.align) return col.align;
  if (!sample || !col.accessor) return "left";
  const v = col.accessor(sample);
  return typeof v === "number" ? "right" : "left";
}

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  onRowClick,
  searchPlaceholder,
  empty,
  exportFilename,
  tableId,
  selectable = false,
  selectionAccessory,
}: Props<T>) {
  const persisted = useMemo(() => loadPersisted(tableId), [tableId]);

  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null);
  const [hidden, setHidden] = useState<Record<string, boolean>>(
    () =>
      persisted.hidden ??
      Object.fromEntries(columns.filter(c => c.hidden).map(c => [c.key, true]))
  );
  const [density, setDensity] = useState<Density>(() => persisted.density ?? "comfortable");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());

  function toggleSelected(id: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function clearSelection() { setSelected(new Set()); }

  useEffect(() => {
    savePersisted(tableId, { hidden, density });
  }, [tableId, hidden, density]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(r =>
      columns.some(c => {
        const v = c.accessor ? c.accessor(r) : (r as any)[c.key];
        return v != null && String(v).toLowerCase().includes(q);
      })
    );
  }, [rows, columns, search]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columns.find(c => c.key === sort.key);
    if (!col) return filtered;
    const acc = col.accessor || ((r: T) => (r as any)[col.key]);
    return [...filtered].sort((a, b) => {
      const av = acc(a);
      const bv = acc(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av < bv ? -1 : av > bv ? 1 : 0) * (sort.dir === "asc" ? 1 : -1);
    });
  }, [filtered, sort, columns]);

  const visibleCols = useMemo(() => columns.filter(c => !hidden[c.key]), [columns, hidden]);
  const sample = rows[0];

  const padCls = density === "compact" ? "py-1" : "py-2";
  const textCls = density === "compact" ? "text-[13px]" : "text-sm";

  function exportCsv() {
    const head = visibleCols.map(c => `"${c.header.replaceAll('"', '""')}"`).join(",");
    const lines = sorted.map(r =>
      visibleCols
        .map(c => {
          const v = c.accessor ? c.accessor(r) : (r as any)[c.key];
          return `"${String(v ?? "").replaceAll('"', '""')}"`;
        })
        .join(",")
    );
    const blob = new Blob([head + "\n" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (exportFilename || "export") + ".csv";
    a.click();
  }

  // All currently-filtered/sorted ids — used for select-all semantics
  // (only the visible filtered rows toggle, not the entire underlying set).
  const visibleIds = useMemo(() => sorted.map(r => rowKey(r)), [sorted, rowKey]);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every(id => selected.has(id));
  function toggleAllVisible() {
    setSelected(prev => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        for (const id of visibleIds) next.delete(id);
      } else {
        for (const id of visibleIds) next.add(id);
      }
      return next;
    });
  }
  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center gap-2 p-2 border-b border-border">
        <input
          className="input max-w-xs"
          placeholder={searchPlaceholder || "Search…"}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {selectable && selectedIds.length > 0 && selectionAccessory && (
          <div className="flex items-center gap-2">
            {selectionAccessory(selectedIds, clearSelection)}
          </div>
        )}
        <button
          type="button"
          className="btn-ghost btn-sm ml-auto"
          title={density === "compact" ? "Switch to comfortable density" : "Switch to compact density"}
          aria-label="Toggle density"
          onClick={() => setDensity(d => (d === "compact" ? "comfortable" : "compact"))}
        >
          {density === "compact" ? <Rows4 size={14} /> : <Rows3 size={14} />}
        </button>
        <details className="relative">
          <summary className="btn cursor-pointer list-none">Columns</summary>
          <div className="absolute right-0 top-full mt-1 z-20 card p-2 min-w-[200px]">
            {columns.map(c => (
              <label key={c.key} className="flex items-center gap-2 px-2 py-1 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={!hidden[c.key]}
                  onChange={() => setHidden(h => ({ ...h, [c.key]: !h[c.key] }))}
                />
                {c.header}
              </label>
            ))}
          </div>
        </details>
        <button className="btn" onClick={exportCsv}>Export CSV</button>
      </div>
      <div className="overflow-auto">
        <table className={cn("table", textCls)}>
          <thead>
            <tr>
              {selectable && (
                <th className={cn(padCls, "w-8 text-center")}>
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={toggleAllVisible}
                    aria-label={allVisibleSelected ? "Deselect all visible" : "Select all visible"}
                  />
                </th>
              )}
              {visibleCols.map(c => {
                const align = defaultAlignFor(c, sample);
                return (
                  <th
                    key={c.key}
                    style={{ width: c.width }}
                    onClick={() =>
                      setSort(s =>
                        s?.key === c.key
                          ? { key: c.key, dir: s.dir === "asc" ? "desc" : "asc" }
                          : { key: c.key, dir: "asc" }
                      )
                    }
                    className={cn(
                      "cursor-pointer select-none",
                      padCls,
                      align === "right" && "text-right",
                      align === "center" && "text-center",
                    )}
                  >
                    {c.header}
                    {sort?.key === c.key && (
                      <span className="text-muted">{sort.dir === "asc" ? " ▲" : " ▼"}</span>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td
                  colSpan={visibleCols.length + (selectable ? 1 : 0)}
                  className="text-center py-8 text-muted"
                >
                  {empty || "No rows."}
                </td>
              </tr>
            )}
            {sorted.map((r, i) => {
              const id = rowKey(r);
              const isSel = selected.has(id);
              return (
              <tr
                key={id}
                onClick={() => onRowClick?.(r)}
                className={cn(
                  onRowClick && "cursor-pointer",
                  // Subtle zebra striping — only odd rows pick up panel2.
                  i % 2 === 1 && "bg-panel2/40",
                  isSel && "bg-accent/10",
                )}
              >
                {selectable && (
                  <td
                    className={cn(padCls, "w-8 text-center")}
                    onClick={e => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={isSel}
                      onChange={() => toggleSelected(id)}
                      aria-label={isSel ? "Deselect row" : "Select row"}
                    />
                  </td>
                )}
                {visibleCols.map(c => {
                  const align = defaultAlignFor(c, sample);
                  return (
                    <td
                      key={c.key}
                      className={cn(
                        padCls,
                        align === "right" && "text-right tabular-nums",
                        align === "center" && "text-center",
                      )}
                    >
                      {c.render ? c.render(r) : String((c.accessor ? c.accessor(r) : (r as any)[c.key]) ?? "")}
                    </td>
                  );
                })}
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-t border-border text-xs text-muted">
        <span>
          {sorted.length === rows.length
            ? `${rows.length} row${rows.length === 1 ? "" : "s"}`
            : `${sorted.length} of ${rows.length} rows`}
        </span>
        {sort && (
          <button
            type="button"
            className="hover:text-text"
            onClick={() => setSort(null)}
          >
            Clear sort
          </button>
        )}
      </div>
    </div>
  );
}
