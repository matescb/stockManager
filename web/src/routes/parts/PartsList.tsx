import { useRef, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Boxes, ImageOff, Loader2, Trash2 } from "lucide-react";
import { api, ApiError, getPaged } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { PagedPartsSchema, PartCategoriesListSchema } from "@/lib/schemas";
import type { Part } from "@/lib/schemas";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import PartsTopNav from "@/components/PartsTopNav";
import { useConfirm } from "@/components/ConfirmDialog";
import QueryStateBoundary from "@/components/QueryStateBoundary";

const PAGE_LIMIT = 50;

export default function PartsList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { workspaceId } = useAuth();
  // Use a distinct key so archived/active lists don't share cache entries.
  const partsKey = useWsKey("parts", "paged", { archived });
  // Parts carry `category_id`, not the name — one list query resolves every
  // row's label. Archived categories are included so a part that still
  // points at one doesn't render a blank cell.
  const categoriesQuery = useQuery({
    queryKey: useWsKey("categories", { archived: true }),
    queryFn: ({ signal }) =>
      api.parsed.get("/categories?include_archived=true", PartCategoriesListSchema, { signal }),
  });
  const categoryNames = new Map(
    (categoriesQuery.data ?? []).map((c) => [c.id, c.name] as const),
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

  const busy = bulkDeleteMutation.isPending;

  // `paged=true` opts into the cursor-paged response shape
  // (`{items, next_cursor}`). Without it, GET /parts returns a bare list
  // for the many lookup-style consumers that still expect Part[]. See
  // backend/app/api/routes/parts.py::list_parts.
  const baseUrl = `/parts?paged=true&limit=${PAGE_LIMIT}${archived ? "&archived=true" : ""}`;

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
      <QueryStateBoundary query={query} resourceLabel="parts">
        {query.isLoading ? (
          <div className="text-muted">Loading…</div>
        ) : (
          <>
            <DataTable
              rows={allParts}
              rowKey={(r) => r.id}
              tableId="parts"
              searchPlaceholder="Search parts…"
              selectable
              selectionAccessory={(ids, clear) => (
                <button
                  type="button"
                  className="btn-danger inline-flex items-center gap-1.5"
                  disabled={busy}
                  onClick={() => doDelete(ids, clear)}
                >
                  <Trash2 size={14} />
                  Delete ({ids.length})
                </button>
              )}
              empty={
                archived ? (
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
              onRowClick={(r) => nav(`/parts/${r.id}/info`)}
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
                  accessor: (r) => (r.category_id ? categoryNames.get(r.category_id) ?? "" : ""),
                  hidden: true,
                },
                { key: "on_hand", header: "Stock", accessor: (r) => r.on_hand ?? 0, width: "80px" },
                {
                  key: "reserved",
                  header: "Reserved",
                  accessor: (r) => r.reserved ?? 0,
                  width: "100px",
                  hidden: true,
                },
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
          </>
        )}
      </QueryStateBoundary>
    </div>
  );
}
