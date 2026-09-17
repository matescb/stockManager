import { useRef, useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Boxes, Loader2, Printer, Trash2 } from "lucide-react";
import { api, ApiError, getPaged } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { PagedPartsSchema } from "@/lib/schemas";
import type { CategoryListSort, Part } from "@/lib/schemas";
import { useCategories } from "@/lib/useCategories";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import PartsTopNav from "@/components/PartsTopNav";
import { useConfirm } from "@/components/ConfirmDialog";
import QueryStateBoundary from "@/components/QueryStateBoundary";
import BatchPrintDialog, { type BatchPrintItem } from "@/routes/labels/BatchPrintDialog";
import { usePanelCollapse } from "@/lib/usePanelCollapse";
import PartsCategoryRail, {
  CATEGORY_RAIL_PANEL_ID,
  PartsCategoryBar,
} from "./PartsCategoryRail";
import { partsListColumns } from "./partsColumns";
import SpecColumnsPicker from "./SpecColumnsPicker";
import { useCategorySpecSchema } from "./useCategorySpecSchema";
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
  // The spec sort is server-side and lives in the URL for the same reason
  // the category filter does: a colleague who is sent "resistors by
  // resistance, descending" should get that list, not the default one.
  const sortKey = searchParams.get("sort");
  const sortDir = searchParams.get("dir") === "desc" ? "desc" : "asc";

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
          // A spec key belongs to one category's schema, so carrying the
          // sort across a category change would ask the server to sort by
          // a key the new category does not have — a 422 on the listing.
          params.delete("sort");
          params.delete("dir");
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

  /** Header click on a spec column: sort by it, or flip the direction. */
  function toggleSpecSort(key: string) {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        const flip = params.get("sort") === key && params.get("dir") !== "desc";
        params.set("sort", key);
        if (flip) params.set("dir", "desc");
        else params.delete("dir");
        return params;
      },
      { replace: true },
    );
  }

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
  // Which of the selected category's spec keys are configured as columns,
  // and which one it sorts by. Resolved server-side (the choice inherits
  // through the category tree), so the list never has to walk it.
  const specSchemaQuery = useCategorySpecSchema(categoryId);
  const specSchema = specSchemaQuery.data;
  const specColumns = useMemo(() => {
    const chosen = specSchema?.list_columns ?? [];
    if (chosen.length === 0) return [];
    const byKey = new Map(specSchema?.keys.map(k => [k.key, k]) ?? []);
    // Ordered by the stored list, and silently dropping a key the schema
    // no longer has — a category renamed away from its schema should lose
    // the column, not render a permanently blank one.
    return chosen.flatMap(key => {
      const spec = byKey.get(key);
      return spec ? [spec] : [];
    });
  }, [specSchema]);
  // A `?sort=` key this category's schema does not have would 422 the
  // whole listing — a stale link, or a category changed under a shared
  // URL. Dropping it is the same call `specColumns` above makes for a
  // stale stored key: the list stays readable and loses the ordering,
  // rather than becoming an error page.
  const validSortKey =
    sortKey !== null && specSchema?.keys.some((spec) => spec.key === sortKey)
      ? sortKey
      : null;
  // What the server is actually ordering by: the URL when it says, and
  // otherwise the category's saved default, which the server applies on
  // its own. The header arrow has to reflect both or a saved default looks
  // like no sort at all.
  const specSort: CategoryListSort | null = validSortKey
    ? { key: validSortKey, dir: sortDir }
    : specSchema?.list_sort ?? null;
  const specColumnParam = specColumns.map((spec) => spec.key).join(",");

  // A distinct key per view so archived/active lists don't share cache
  // entries, and so a filtered or sorted list is its own entry rather than
  // a rearranged view of an unfiltered one.
  const partsKey = useWsKey("parts", "paged", {
    archived,
    categoryId,
    includeDescendants,
    // All three change the request, so all three must change the key —
    // `specColumnParam` especially: it is resolved from the category
    // (asynchronously, and again after every picker toggle), so without it
    // here the request URL would move while the key stood still and
    // TanStack would keep serving the page that has no spec values on it.
    specColumnParam,
    validSortKey,
    sortDir,
  });
  // The column set is data, not markup — see `partsColumns.tsx`. Memoised
  // because `DataTable` filters and sorts through the array on every
  // render, and a fresh one each keystroke would rebuild the category path
  // lookup with it.
  const columns = useMemo(
    () => partsListColumns({ categories, specColumns, specSort, onSpecSort: toggleSpecSort }),
    // `toggleSpecSort` closes over `setSearchParams` only, which is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [categories, specColumns, specSort?.key, specSort?.dir],
  );

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

  // "Save as default sort" — persists the current spec sort on the
  // category so everyone in the workspace gets it. Separate from the
  // picker's mutation because it is a different field and a different
  // button; they share the invalidation targets, not the request.
  const saveSortMutation = useApiMutation<unknown, { list_sort: CategoryListSort | null }>({
    mutationKey: ["category", categoryId ?? "none", "list-sort"],
    mutationFn: (payload) => api.patch(`/categories/${categoryId}`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "categories") });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
      toast.success("Saved as this category's default sort.");
    },
    onError: (e) => {
      toast.error(e instanceof ApiError ? e.userMessage : "Could not save the sort");
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
        (includeDescendants ? "" : "&include_descendants=false") +
        // The values the spec columns render, and the server-side order.
        // Both are ignored without `category_id`, so both are built here.
        (specColumnParam
          ? `&spec_columns=${encodeURIComponent(specColumnParam)}`
          : "") +
        (validSortKey
          ? `&sort=${encodeURIComponent(`spec:${validSortKey}`)}&dir=${sortDir}`
          : "")
      : "");

  // With a category selected the request URL depends on that category's
  // configured spec columns, which arrive asynchronously — so firing the
  // list before the schema lands fetches the page twice, once without the
  // columns and once with. `isFetched` is true after an error too, so a
  // schema that fails to load still shows the list, without them.
  const specSchemaReady = categoryId === null || specSchemaQuery.isFetched;

  const query = useInfiniteQuery({
    queryKey: partsKey,
    enabled: specSchemaReady,
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

  // Master-detail: a row click selects into the preview pane once the
  // viewport is wide enough, and navigates to the full part page below
  // that exactly as it always did. Selection lives in `?sel=<id>`,
  // alongside the category filter's `?category=` — both writers use a
  // functional `setSearchParams` updater over the previous params, so
  // neither clobbers the other.
  //
  // Collapsing the rail hands its 240px back to the row, which is what
  // lets the pane appear at `lg` instead of waiting for `xl`; the rail's
  // state is therefore owned here rather than inside the rail itself.
  const categoryRail = usePanelCollapse(CATEGORY_RAIL_PANEL_ID);
  const preview = usePartPreview(allParts, {
    railCollapsed: categoryRail.collapsed,
  });

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

  // Names for the "inherited from <parent>" lines on the spec-column
  // controls — the schema endpoint answers with ids, and the rail already
  // fetched every category.
  const categoryNames = useMemo(
    () => new Map(categories.map((c) => [c.id, c.name] as const)),
    [categories],
  );

  // The sort on screen differs from the category's saved default, so
  // offering to store it is not a no-op. Also covers "no default yet".
  const sortIsUnsaved =
    specSort !== null &&
    (specSchema?.list_sort?.key !== specSort.key ||
      specSchema?.list_sort?.dir !== specSort.dir);

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
        <PartsCategoryRail
          {...categoryFilterProps}
          collapsed={categoryRail.collapsed}
          onToggleCollapsed={categoryRail.toggle}
        />
        <div className="flex-1 min-w-0">
          <PartsCategoryBar {...categoryFilterProps} />
          {categoryId && specSchema && (
            <div className="flex flex-wrap items-center gap-2 pb-2">
              <SpecColumnsPicker
                categoryId={categoryId}
                schema={specSchema}
                categoryNames={categoryNames}
              />
              {sortIsUnsaved && (
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={saveSortMutation.isPending}
                  onClick={() => saveSortMutation.mutate({ list_sort: specSort })}
                >
                  Save as default sort
                </button>
              )}
              {specSchema.sort_inherited_from && !sortKey && (
                <span className="text-xs text-muted">
                  Default sort inherited from{" "}
                  {categoryNames.get(specSchema.sort_inherited_from) ??
                    "a parent category"}
                </span>
              )}
            </div>
          )}
          <QueryStateBoundary query={query} resourceLabel="parts">
            {query.isLoading || !specSchemaReady ? (
              // `enabled: false` leaves the query "pending but not
              // fetching", which `isLoading` reads as false — without the
              // second half this flashes the empty state before the first
              // request has even been made.
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
                  columns={columns}
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
