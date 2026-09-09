/**
 * The `/parts` table's column set.
 *
 * Lifted out of `PartsList.tsx` when the list grew from eight columns to
 * eighteen: the list component is about fetching, selection and the
 * preview pane, and a column definition is about one field. Splitting
 * them also makes the columns unit-testable without mounting the route —
 * `__tests__/partsColumns.test.tsx` asserts each accessor and renderer
 * directly.
 *
 * ## Hidden by default
 *
 * Most of what is added here ships `hidden: true`. `DataTable`'s
 * `initialHiddenFor` merges the persisted per-workspace map *over* the
 * declared defaults, so a user who has explicitly toggled a column keeps
 * their choice and everyone else gets the short table they had before.
 * The columns are available in the "Columns" menu — which is the ask:
 * more things you *can* show, not more things you must look at.
 *
 * ## accessor vs render
 *
 * `DataTable` sorts, searches and CSV-exports the `accessor`, and paints
 * the `render`. Two traps, both of which have bitten this repo:
 *
 *   - a *formatted* accessor sorts as text, so `"10 m"` lands before
 *     `"9 m"` — quantities therefore go through `quantityColumn`, which
 *     keeps a numeric accessor and a formatted renderer;
 *   - a `render` with no `accessor` exports an *empty* CSV cell, so every
 *     render-only column below still declares one.
 *
 * Dates use `formatDate` (`YYYY-MM-DD`) as the accessor on purpose: it
 * sorts lexicographically in chronological order, and CSV then matches
 * what is on screen. The full timestamp rides along in a `title`.
 */
import type { ReactNode } from "react";
import { ImageOff } from "lucide-react";

import { categoryPath } from "@/lib/categoryTree";
import { formatDate, formatDateTime } from "@/lib/format";
import { providerLabel } from "@/lib/providers";
import type { Part, PartCategory } from "@/lib/schemas";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";
import { type Column, quantityColumn } from "@/components/DataTable";

/** Booleans read better as words than as `true` / `false` in a CSV. */
function yesNo(value: boolean | null | undefined): string {
  return value ? "Yes" : "No";
}

/** A date cell: sortable `YYYY-MM-DD` on screen, full timestamp on hover. */
function dateCell(iso: string | null | undefined): ReactNode {
  const short = formatDate(iso);
  if (!short) return "";
  return <span title={formatDateTime(iso)}>{short}</span>;
}

/**
 * The distributor links for one row.
 *
 * `provider_links` is optional on the wire: absent means the response
 * never loaded them, `[]` means this part has none. Both render blank
 * here — the difference matters to the schema, not to the operator — but
 * the list endpoint does load them, so a blank cell on `/parts` really
 * does mean "no distributor knows this part".
 */
function distributorLinks(row: Part): ReactNode {
  const links = (row.provider_links ?? []).filter(link =>
    isSafeHttpOrSameOriginUrl(link.source_url),
  );
  if (links.length === 0) return distributorNames(row) || "";
  return (
    <span className="flex flex-wrap gap-x-2 gap-y-0.5">
      {links.map(link => (
        <a
          key={link.provider}
          className="text-accent hover:underline"
          href={link.source_url as string}
          target="_blank"
          rel="noopener noreferrer"
          // The row itself is clickable (it drives the preview pane);
          // without this, following a link would also select the row.
          onClick={event => event.stopPropagation()}
        >
          {providerLabel(link.provider)}
        </a>
      ))}
    </span>
  );
}

/** What the Distributors column sorts, searches and exports on. */
function distributorNames(row: Part): string {
  return (row.provider_links ?? [])
    .map(link => providerLabel(link.provider))
    .join(", ");
}

export type PartsColumnOptions = {
  /**
   * The workspace's categories, archived ones included. Rows carry only
   * `category_id`; the name is resolved against this list rather than by
   * a server-side join, because `PartsList` already fetches it for the
   * category rail — a join would put an extra lookup on every page of the
   * busiest endpoint in the app to render a column that is hidden by
   * default.
   */
  categories: readonly PartCategory[];
};

export function partsListColumns({ categories }: PartsColumnOptions): Column<Part>[] {
  const categoryNames = new Map(categories.map(c => [c.id, c.name] as const));

  return [
    {
      key: "image",
      header: "",
      width: "44px",
      render: r => {
        const safeImageUrl = isSafeHttpOrSameOriginUrl(r.image_url) ? r.image_url : null;
        return safeImageUrl ? (
          <img
            src={safeImageUrl}
            alt=""
            loading="lazy"
            className="h-8 w-8 object-contain rounded bg-panel"
          />
        ) : (
          <div className="h-8 w-8 rounded bg-panel2/40 flex items-center justify-center text-muted">
            <ImageOff size={14} />
          </div>
        );
      },
    },
    { key: "part_type", header: "Type", accessor: r => r.part_type, width: "100px" },
    {
      key: "name",
      header: "Part",
      accessor: r => r.name,
      render: r => <span className="font-medium">{r.name}</span>,
    },
    { key: "mpn", header: "MPN", accessor: r => r.mpn ?? "" },
    {
      key: "internal_part_number",
      header: "Internal P/N",
      headerLabel: "Internal P/N",
      accessor: r => r.internal_part_number ?? "",
      hidden: true,
    },
    { key: "manufacturer", header: "Manufacturer", accessor: r => r.manufacturer ?? "" },
    { key: "footprint", header: "Footprint", accessor: r => r.footprint ?? "" },
    {
      key: "category",
      header: "Category",
      // Full path ("Passives / Resistors"), not the leaf name: with a
      // tree, two branches may hold same-named leaves, and this string is
      // what search and CSV export see.
      accessor: r =>
        r.category_id
          ? categoryPath(categories, r.category_id) ||
            categoryNames.get(r.category_id) ||
            ""
          : "",
      hidden: true,
    },
    quantityColumn<Part>({
      key: "on_hand",
      header: "Stock",
      value: r => r.on_hand ?? 0,
      width: "80px",
    }),
    quantityColumn<Part>({
      key: "reserved",
      header: "Reserved",
      value: r => r.reserved ?? 0,
      width: "100px",
      hidden: true,
    }),
    quantityColumn<Part>({
      key: "available",
      header: "Available",
      value: r => r.available ?? 0,
      width: "100px",
      hidden: true,
    }),
    quantityColumn<Part>({
      key: "low_stock_report_quantity",
      header: "Low-stock at",
      headerLabel: "Low-stock at",
      // Null means "no threshold set", which is not the same as zero —
      // `formatQuantity`'s default renders it blank rather than as `0`.
      value: r => r.low_stock_report_quantity,
      width: "110px",
      hidden: true,
    }),
    {
      key: "linked_provider",
      header: "Provider",
      accessor: r => providerLabel(r.linked_provider),
      hidden: true,
    },
    {
      key: "provider_links",
      header: "Distributors",
      accessor: distributorNames,
      render: distributorLinks,
      hidden: true,
    },
    {
      key: "published",
      header: "Published",
      accessor: r => yesNo(r.published),
      width: "100px",
      hidden: true,
    },
    {
      key: "serialized",
      header: "Serialized",
      accessor: r => yesNo(r.serialized),
      width: "100px",
      hidden: true,
    },
    {
      key: "last_refresh_at",
      header: "Last refresh",
      accessor: r => formatDate(r.last_refresh_at),
      render: r => dateCell(r.last_refresh_at),
      width: "120px",
      hidden: true,
    },
    {
      key: "updated_at",
      header: "Last change",
      accessor: r => formatDate(r.updated_at),
      render: r => dateCell(r.updated_at),
      width: "120px",
      hidden: true,
    },
  ];
}
