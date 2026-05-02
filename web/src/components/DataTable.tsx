import { ReactNode, useEffect, useMemo, useState } from "react";
import { Rows3, Rows4 } from "lucide-react";
import { cn } from "@/lib/cn";

// ---------------------------------------------------------------------
// CSV-export helpers — extracted so they can be unit-tested without
// rendering the table. The hardening is per FE2-008:
//
//   1. Excel formula-injection mitigation: any cell whose first char is
//      `=`, `+`, `-`, `@`, or a leading tab/CR (per CWE-1236) gets
//      prefixed with a single quote, which neutralises the formula
//      while still being readable.
//   2. Doubled quotes for embedded `"` (RFC 4180).
//   3. CRLF line terminators so Excel and Windows text editors don't
//      smush rows together.
//   4. UTF-8 BOM up front so Excel auto-detects the encoding instead
//      of mangling non-ASCII to mojibake.
// ---------------------------------------------------------------------

const FORMULA_INJECTION_LEADERS = new Set(["=", "+", "-", "@", "\t", "\r"]);

export function escapeCsvCell(raw: unknown): string {
  // null / undefined → empty cell. Booleans + numbers get stringified
  // verbatim (no leading-`-` neutralisation needed for negatives —
  // those are legitimate numeric data, but Excel still treats them as
  // formula-leading text. We only neutralise *string* cells whose first
  // char is risky, since those are the user-supplied free-form fields.)
  if (raw == null) return '""';
  let s = String(raw);
  if (s.length > 0 && FORMULA_INJECTION_LEADERS.has(s[0]) && typeof raw === "string") {
    s = "'" + s;
  }
  return `"${s.replaceAll('"', '""')}"`;
}

export function buildCsv(headers: string[], rows: string[][]): string {
  const head = headers.map(h => escapeCsvCell(h)).join(",");
  const body = rows.map(r => r.map(c => escapeCsvCell(c)).join(",")).join("\r\n");
  // U+FEFF BOM so Excel detects UTF-8 instead of misreading as cp1252.
  return "﻿" + head + "\r\n" + body;
}

/** Prune a selection set to ids that still appear in the row list. */
export function pruneSelection(
  selected: ReadonlySet<string>,
  rowIds: ReadonlyArray<string>,
): Set<string> {
  const visible = new Set(rowIds);
  const next = new Set<string>();
  for (const id of selected) if (visible.has(id)) next.add(id);
  return next;
}

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

  // FE2-007 — clear the selection set whenever the table changes
  // identity. Without this, navigating from /parts to /orders carried
  // a stale selection of part ids that no longer mapped to anything,
  // and the bulk-action button would happily try to delete random
  // rows from the new view.
  useEffect(() => {
    setSelected(new Set());
  }, [tableId]);

  // FE2-007 — prune ids of rows that no longer exist after a refetch
  // or filter narrowing. The bulk-delete dialog reads from `selected`,
  // so leaving stale ids in here meant the action could attempt to
  // delete items the user couldn't see.
  const allRowIds = useMemo(() => rows.map(r => rowKey(r)), [rows, rowKey]);
  useEffect(() => {
    setSelected(prev => {
      const pruned = pruneSelection(prev, allRowIds);
      // Only update state if something actually changed (avoids an
      // infinite re-render loop when the row list is stable).
      if (pruned.size === prev.size) return prev;
      return pruned;
    });
  }, [allRowIds]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(r =>
      columns.some(c => {
        // FIXME: typed row-access requires a generic constraint refactor
        // (e.g. `T extends Record<string, unknown>`). Deferred — see issue #57.
        const v = c.accessor ? c.accessor(r) : (r as Record<string, unknown>)[c.key];
        return v != null && String(v).toLowerCase().includes(q);
      })
    );
  }, [rows, columns, search]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columns.find(c => c.key === sort.key);
    if (!col) return filtered;
    // FIXME: typed row-access requires a generic constraint refactor — see issue #57.
    const acc = col.accessor || ((r: T) => (r as Record<string, unknown>)[col.key]);
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

  function cellText(c: Column<T>, r: T): string {
    // Prefer the structured accessor — it returns the raw scalar
    // (string / number / bool) without React-rendered HTML.
    if (c.accessor) {
      const v = c.accessor(r);
      return v == null ? "" : String(v);
    }
    // No accessor + no render: dumb-key access (matches the on-screen
    // table cell). Avoid `[object Object]` for non-scalars by checking
    // the type before stringifying.
    if (!c.render) {
      // FIXME: typed row-access requires a generic constraint refactor — see issue #57.
      const v = (r as Record<string, unknown>)[c.key];
      if (v == null) return "";
      const t = typeof v;
      if (t === "string" || t === "number" || t === "boolean") return String(v);
      return "";
    }
    // Render-only column with no accessor — we have no safe way to
    // pull text out of an arbitrary ReactNode without rendering it,
    // so return empty rather than `[object Object]`. Authors who
    // want a column to round-trip through CSV should set `accessor`.
    return "";
  }

  function exportCsv() {
    const head = visibleCols.map(c => c.header);
    const body = sorted.map(r => visibleCols.map(c => cellText(c, r)));
    const csv = buildCsv(head, body);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = (exportFilename || "export") + ".csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Free the blob URL once the click has been dispatched. Browsers
    // can keep the reference around forever otherwise (memory leak on
    // long-lived sessions that export repeatedly).
    URL.revokeObjectURL(url);
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
                      {/* FIXME: typed row-access requires a generic constraint refactor — see issue #57. */}
                      {c.render ? c.render(r) : String((c.accessor ? c.accessor(r) : (r as Record<string, unknown>)[c.key]) ?? "")}
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
