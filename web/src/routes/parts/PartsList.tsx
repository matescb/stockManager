import { useRef, useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Boxes, ImageOff, Loader2, Printer, Trash2 } from "lucide-react";
import { api, ApiError, getPaged } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { PagedPartsSchema } from "@/lib/schemas";
import type { Part } from "@/lib/schemas";
import { useCategories } from "@/lib/useCategories";
import { categoryPath } from "@/lib/categoryTree";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";
import { DataTable, quantityColumn } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import PartsTopNav from "@/components/PartsTopNav";
import { useConfirm } from "@/components/ConfirmDialog";
import QueryStateBoundary from "@/components/QueryStateBoundary";
import BatchPrintDialog, { type BatchPrintItem } from "@/routes/labels/BatchPrintDialog";
import PartsCategoryRail, { PartsCategoryBar } from "./PartsCategoryRail";
import PartsPreviewLayout from "@/routes/parts/preview/PartsPreviewLayout";
import { usePartPreview } from "@/routes/parts/preview/usePartPreview";

const PAGE_LIMIT = 50;

export default function PartsList({ archived = false }: { archived?: boolean }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { workspaceId } = useAuth();

  // Category filter lives in the URL so a filtered list is deep-linkable
  // and back/forward works — the same reason `App.tsx` preserves search
  // across the login round-trip (issue #304). `exact=1` opts out of
  // descendant expansion, which the API includes by default.
  const [searchParams, setSearchParams] = useSearchParams();
  const categoryId = searchParams.get("category");
  const includeDescendants = searchParams.get("exact") !== "1";

  function updateCategoryParams(next: { id?: string | null; exact?: boolean }) {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if ("id" in next) {
          if (next.id) params.set("category", next.id);
          else {
            params.delete("category");
            params.delete("exact");
          }
        }
        if ("exact" in next) {
          if (next.exact === false) params.set("exact", "1");
          else params.delete("exact");
        }
        return params;
      },
      { replace: true },
    );
  }

  // Use a distinct key so archived/active lists don't share cache entries,
  // and so a category filter is its own cache entry rather than a filtered
  // view of an unfiltered one.
  const partsKey = useWsKey("parts", "paged", {
    archived,
    categoryId,
    includeDescendants,
  });
  // Parts carry `category_id`, not the name — one list query resolves every
  // row's label. Archived categories are included so a part that still
  // points at one doesn't render a blank cell.
  const categoriesQuery = useCategories({ includeArchived: true });
  // Memoised: a fresh `[]` each render would rebuild the rail's whole tree
  // on every keystroke in the table's search box.
  const categories = useMemo(
    () => categoriesQuery.data ?? [],
    [categoriesQuery.data],
  );
  const categoryNames = new Map(categories.map((c) => [c.id, c.name] as const));

  const bulkDeleteMutation = useApiMutation<{ archived_ids: string[]; skipped: number }, { part_ids: string[] }>({
    mutationKey: ["parts", "bulk-delete"],
    mutationFn: (payload) =>
      api.post<{ archived_ids: string[]; skipped: number }>("/parts/bulk-delete", payload),
    onSuccess: (res, payload) => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
      const ids = payload.part_ids;
      toast.success(
        res.archived_ids.length === ids.length
          ? `Archived ${res.archived_ids.length} part${res.archived_ids.length === 1 ? "" : "s"}.`
          : `Archived ${res.archived_ids.length} of ${ids.length}; ${res.skipped} skipped.`,
      );
    },
    onError: (e) => {
      toast.error(e instanceof ApiError ? e.userMessage : "Bulk delete failed");
    },
  });

  const busy = bulkDeleteMutation.isPending;

  // `paged=true` opts into the cursor-paged response shape
  // (`{items, next_cursor}`). Without it, GET /parts returns a bare list
  // for the many lookup-style consumers that still expect Part[]. See
  // backend/app/api/routes/parts.py::list_parts.
  //
  // The category predicate goes on the URL, not on the rows we get back:
  // the server applies it before `paginate()` (its cursor is an
  // HMAC-signed seek position, so a client-side filter would give short
  // pages and a misleading "load more").
  const baseUrl =
    `/parts?paged=true&limit=${PAGE_LIMIT}` +
    (archived ? "&archived=true" : "") +
    (categoryId
      ? `&category_id=${encodeURIComponent(categoryId)}` +
        (includeDescendants ? "" : "&include_descendants=false")
      : "");

  const query = useInfiniteQuery({
    queryKey: partsKey,
    queryFn: async ({ pageParam, signal }) => {
      const url = pageParam ? `${baseUrl}&cursor=${encodeURIComponent(pageParam)}` : baseUrl;
      const raw = await getPaged<unknown>(url, { signal });
      // Validate the page against the Zod schema.
      const parsed = PagedPartsSchema.safeParse(raw);
      if (!parsed.success) {
        // Shape drift — surface as ApiError so QueryStateBoundary catches it.
        throw new ApiError(
          0,
          { data: null, status: { category: "client_schema_mismatch", message: "API response shape changed" } },
          "Parts API response did not match expected schema",
        );
      }
      return parsed.data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  // Flatten all loaded pages into a single array for the DataTable.
  const allParts: Part[] = (query.data?.pages ?? []).flatMap((p) => p.items);
  const hasNextPage = query.hasNextPage;
  const isFetchingNextPage = query.isFetchingNextPage;

  // Master-detail: a row click selects into the preview pane at `xl` and
  // wider, and navigates to the full part page below it exactly as it
  // always did. Selection lives in `?sel=<id>`, alongside the category
  // filter's `?category=` — both writers use a functional `setSearchParams`
  // updater over the previous params, so neither clobbers the other.
  const preview = usePartPreview(allParts);

  // IntersectionObserver sentinel — auto-load next page when the user
  // scrolls to the bottom of the table.
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const sentinelCallback = useCallback(
    (node: HTMLDivElement | null) => {
      if (observerRef.current) observerRef.current.disconnect();
      sentinelRef.current = node;
      if (!node) return;
      observerRef.current = new IntersectionObserver(
        (entries) => {
          if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
            query.fetchNextPage();
          }
        },
        { threshold: 0.1 },
      );
      observerRef.current.observe(node);
    },
    [hasNextPage, isFetchingNextPage, query],
  );

  // Batch label printing for the current selection. The dialog owns the
  // template choice, the per-object loop and the printer-failure message;
  // this list only supplies which objects were picked.
  const [batchPrint, setBatchPrint] = useState<{
    items: BatchPrintItem[];
    clear: () => void;
  } | null>(null);

  function openBatchPrint(ids: string[], clear: () => void) {
    const partsById = new Map(allParts.map((p) => [p.id, p]));
    setBatchPrint({
      items: ids.map((id) => ({ id, label: partsById.get(id)?.name ?? id })),
      clear,
    });
  }

  async function doDelete(ids: string[], clear: () => void) {
    const partsById = new Map(allParts.map((p) => [p.id, p]));
    const previewLines = ids
      .slice(0, 12)
      .map((id) => partsById.get(id)?.name ?? id)
      .join("\n");
    const more = ids.length > 12 ? `\n…and ${ids.length - 12} more` : "";
    const ok = await confirm({
      title: `Archive ${ids.length} part${ids.length === 1 ? "" : "s"}?`,
      message:
        "Stock history is preserved. Archived parts can be restored from the Archived view.\n\n" +
        previewLines +
        more,
      severity: "danger",
      confirmLabel: "Archive",
    });
    if (!ok) return;
    clear();
    bulkDeleteMutation.mutate({ part_ids: ids });
  }

  // Both shapes of the filter (rail at lg+, select below it) are wired to
  // the same handlers.
  const categoryFilterProps = {
    categories,
    selectedId: categoryId,
    onSelect: (id: string | null) => updateCategoryParams({ id }),
    includeDescendants,
    onIncludeDescendantsChange: (exact: boolean) => updateCategoryParams({ exact }),
  };

  return (
    <div>
      <PartsTopNav
        rightAccessory={
          <>
            <Link to="/parts/scan-import" className="btn">Scan</Link>
            <Link to="/parts/create" className="btn-primary">+ Part</Link>
          </>
        }
      />
      <div className="flex gap-4 items-start">
        <PartsCategoryRail {...categoryFilterProps} />
        <div className="flex-1 min-w-0">
          <PartsCategoryBar {...categoryFilterProps} />
          <QueryStateBoundary query={query} resourceLabel="parts">
            {query.isLoading ? (
              <div className="text-muted">Loading…</div>
            ) : (
              <PartsPreviewLayout preview={preview}>
                <DataTable
                  rows={allParts}
                  rowKey={(r) => r.id}
                  tableId="parts"
                  searchPlaceholder="Search parts…"
                  selectable
                  selectionAccessory={(ids, clear) => (
                    <>
                      <button
                        type="button"
                        className="btn inline-flex items-center gap-1.5"
                        onClick={() => openBatchPrint(ids, clear)}
                      >
                        <Printer size={14} />
                        Print labels ({ids.length})
                      </button>
                      <button
                        type="button"
                        className="btn-danger inline-flex items-center gap-1.5"
                        disabled={busy}
                        onClick={() => doDelete(ids, clear)}
                      >
                        <Trash2 size={14} />
                        Delete ({ids.length})
                      </button>
                    </>
                  )}
                  empty={
                    categoryId ? (
                      // "Create your first part" would be wrong here — the
                      // workspace may be full of parts that simply aren't in
                      // this branch. Offer the way out instead.
                      <EmptyState
                        icon={Boxes}
                        title="No parts in this category"
                        description={
                          includeDescendants
                            ? "Nothing is filed here or in any subcategory."
                            : "Nothing is filed directly here. Tick “Include subcategories” to search beneath it."
                        }
                        action={{ label: "Show all parts", to: "/parts" }}
                      />
                    ) : archived ? (
                      <EmptyState
                        icon={Boxes}
                        title="No archived parts"
                        description="Archived parts will appear here."
                      />
                    ) : (
                      <EmptyState
                        icon={Boxes}
                        title="No parts yet"
                        description="Create your first part to start tracking stock."
                        action={{ label: "+ Part", to: "/parts/create" }}
                      />
                    )
                  }
                  exportFilename="parts"
                  onRowClick={preview.openRow}
                  onRowFocusChange={preview.previewRow}
                  rowClassName={preview.rowClassName}
                  columns={[
                    {
                      key: "image",
                      header: "",
                      width: "44px",
                      render: (r) => {
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
                    { key: "part_type", header: "Type", accessor: (r) => r.part_type, width: "100px" },
                    {
                      key: "name",
                      header: "Part",
                      accessor: (r) => r.name,
                      render: (r) => <span className="font-medium">{r.name}</span>,
                    },
                    { key: "mpn", header: "MPN", accessor: (r) => r.mpn ?? "" },
                    { key: "manufacturer", header: "Manufacturer", accessor: (r) => r.manufacturer ?? "" },
                    { key: "footprint", header: "Footprint", accessor: (r) => r.footprint ?? "" },
                    {
                      key: "category",
                      header: "Category",
                      // Full path ("Passives / Resistors"), not the leaf name:
                      // with a tree, two branches may hold same-named leaves,
                      // and this string is what search and CSV export see.
                      accessor: (r) =>
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
                      value: (r) => r.on_hand ?? 0,
                      width: "80px",
                    }),
                    quantityColumn<Part>({
                      key: "reserved",
                      header: "Reserved",
                      value: (r) => r.reserved ?? 0,
                      width: "100px",
                      hidden: true,
                    }),
                  ]}
                />

                {/* Infinite-scroll sentinel and load-more footer */}
                {hasNextPage && (
                  <div
                    ref={sentinelCallback}
                    className="flex items-center justify-center gap-2 py-3 text-sm text-muted"
                  >
                    {isFetchingNextPage ? (
                      <>
                        <Loader2 size={14} className="animate-spin" />
                        Loading more parts…
                      </>
                    ) : (
                      <button
                        type="button"
                        className="btn"
                        onClick={() => query.fetchNextPage()}
                      >
                        Load more
                      </button>
                    )}
                  </div>
                )}
              </PartsPreviewLayout>
            )}
          </QueryStateBoundary>
        </div>
      </div>

      <BatchPrintDialog
        open={batchPrint !== null}
        entityType="part"
        items={batchPrint?.items ?? []}
        onClose={() => setBatchPrint(null)}
        onDone={() => {
          batchPrint?.clear();
          setBatchPrint(null);
        }}
      />
    </div>
  );
}
