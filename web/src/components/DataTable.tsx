import { ReactNode, useMemo, useState } from "react";
import { cn } from "@/lib/cn";

export type Column<T> = {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  accessor?: (row: T) => string | number | boolean | null | undefined;
  width?: string;
  hidden?: boolean;
};

type Props<T> = {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  searchPlaceholder?: string;
  initialSearch?: string;
  empty?: ReactNode;
  exportFilename?: string;
};

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  onRowClick,
  searchPlaceholder,
  empty,
  exportFilename,
}: Props<T>) {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null);
  const [hidden, setHidden] = useState<Record<string, boolean>>(
    Object.fromEntries(columns.filter(c => c.hidden).map(c => [c.key, true]))
  );

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

  function exportCsv() {
    const visible = columns.filter(c => !hidden[c.key]);
    const head = visible.map(c => `"${c.header.replaceAll('"', '""')}"`).join(",");
    const lines = sorted.map(r =>
      visible
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

  const visibleCols = columns.filter(c => !hidden[c.key]);

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center gap-2 p-2 border-b border-border">
        <input
          className="input max-w-xs"
          placeholder={searchPlaceholder || "Search…"}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <details className="ml-auto relative">
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
        <table className="table">
          <thead>
            <tr>
              {visibleCols.map(c => (
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
                  className="cursor-pointer select-none"
                >
                  {c.header}
                  {sort?.key === c.key && (sort.dir === "asc" ? " ▲" : " ▼")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={visibleCols.length} className="text-center py-8 text-muted">
                  {empty || "No rows."}
                </td>
              </tr>
            )}
            {sorted.map(r => (
              <tr
                key={rowKey(r)}
                onClick={() => onRowClick?.(r)}
                className={cn(onRowClick && "cursor-pointer")}
              >
                {visibleCols.map(c => (
                  <td key={c.key}>
                    {c.render ? c.render(r) : String((c.accessor ? c.accessor(r) : (r as any)[c.key]) ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
