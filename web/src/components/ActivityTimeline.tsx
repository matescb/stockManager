import { useInfiniteQuery } from "@tanstack/react-query";
import {
  Plus,
  Minus,
  Move,
  RotateCcw,
  ShoppingBag,
  Hammer,
  Edit3,
  FilePlus2,
  Lock,
  Unlock,
  Activity as ActivityIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import {
  formatDate,
  formatDateTime,
  formatQuantity,
  formatQuantityPhrase,
} from "@/lib/format";

type ActivityKind =
  | "stock"
  | "part_created"
  | "part_updated"
  | "order_created"
  | "order_updated"
  | "build_created"
  | "build_updated";

type OperationType =
  | "add"
  | "remove"
  | "move_out"
  | "move_in"
  | "adjust"
  | "receive"
  | "build_consume"
  | "build_produce"
  | "reserve"
  | "release"
  | null;

export type ActivityEntry = {
  kind: ActivityKind;
  operation_type: OperationType;
  quantity_delta: number | null;
  /**
   * The ledger row's own unit stamp — not the part's current
   * `unit_of_measure`. A timeline is a list of historical facts, so
   * re-resolving the unit at read time would relabel history the moment
   * a part's unit changed. `null` on non-stock entries.
   */
  unit: string | null;
  user: { id: string; name: string } | null;
  occurred_at: string;
  comments: string | null;
  lot_id: string | null;
  storage_location_id: string | null;
  order_id: string | null;
  build_id: string | null;
};

type ActivityPage = {
  events: ActivityEntry[];
  next_before_occurred_at?: string;
  next_before_id?: string;
};

type Props = {
  /** Already-encoded path, e.g. `/parts/<id>/activity`. */
  endpoint: string;
};

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffMs = Date.now() - then;
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day}d ago`;
  return formatDate(iso);
}

function iconFor(e: ActivityEntry): ReactNode {
  if (e.kind.endsWith("_created")) return <FilePlus2 className="w-4 h-4" />;
  if (e.kind.endsWith("_updated")) return <Edit3 className="w-4 h-4" />;
  switch (e.operation_type) {
    case "add":
      return <Plus className="w-4 h-4 text-accent" />;
    case "remove":
      return <Minus className="w-4 h-4 text-danger" />;
    case "move_out":
    case "move_in":
      return <Move className="w-4 h-4" />;
    case "adjust":
      return <RotateCcw className="w-4 h-4" />;
    case "receive":
      return <ShoppingBag className="w-4 h-4 text-accent" />;
    case "build_consume":
    case "build_produce":
      return <Hammer className="w-4 h-4" />;
    case "reserve":
      return <Lock className="w-4 h-4 text-warning" />;
    case "release":
      return <Unlock className="w-4 h-4 text-muted" />;
    default:
      return <ActivityIcon className="w-4 h-4" />;
  }
}

/**
 * One-line description of an activity entry.
 *
 * Exported so it can be unit-tested without mounting the timeline (the
 * component brings a QueryClient and an infinite-scroll fetch with it) —
 * same rationale as `DataTable`'s extracted CSV helpers.
 */
export function activitySummary(e: ActivityEntry): string {
  if (e.kind === "part_created") return "Part created";
  if (e.kind === "part_updated") return "Part updated";
  if (e.kind === "order_created") return "Order created";
  if (e.kind === "order_updated") return "Order updated";
  if (e.kind === "build_created") return "Build created";
  if (e.kind === "build_updated") return "Build updated";

  const q = e.quantity_delta;
  // `formatQuantityPhrase` keeps the English noun ("12 units") while the
  // part is counted in the default `pcs`, and swaps it for the unit code
  // ("12.5 m") once the part is measured — "12.5 metres units" is not a
  // sentence. Both render the number exactly; neither truncates.
  const amount = q != null ? formatQuantityPhrase(Math.abs(q), e.unit) : "";
  switch (e.operation_type) {
    case "add":
      return `Added ${amount}`;
    case "remove":
      return `Removed ${amount}`;
    case "move_out":
      return `Moved out ${amount}`;
    case "move_in":
      return `Moved in ${amount}`;
    case "adjust":
      return `Adjusted by ${q != null && q > 0 ? "+" : ""}${formatQuantity(q ?? 0, e.unit)}`;
    case "receive":
      return `Received ${amount}`;
    case "build_consume":
      return `Consumed ${amount} for build`;
    case "build_produce":
      return `Build produced ${amount}`;
    case "reserve":
      return `Reserved ${amount}`;
    case "release":
      return `Released ${amount}`;
    default:
      return e.operation_type ?? "Stock event";
  }
}

export default function ActivityTimeline({ endpoint }: Props) {
  const {
    data,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery<ActivityPage>({
    queryKey: useWsKey("activity", endpoint),
    queryFn: ({ pageParam, signal }) => {
      let url = endpoint;
      if (
        pageParam &&
        typeof pageParam === "object" &&
        "before_occurred_at" in pageParam &&
        "before_id" in pageParam
      ) {
        const p = pageParam as { before_occurred_at: string; before_id: string };
        const qs = new URLSearchParams({
          before_occurred_at: p.before_occurred_at,
          before_id: p.before_id,
        });
        url = `${endpoint}?${qs.toString()}`;
      }
      return api.get<ActivityPage>(url, { signal });
    },
    initialPageParam: null,
    getNextPageParam: (lastPage) => {
      if (lastPage.next_before_occurred_at && lastPage.next_before_id) {
        return {
          before_occurred_at: lastPage.next_before_occurred_at,
          before_id: lastPage.next_before_id,
        };
      }
      return undefined;
    },
  });

  const allEvents = data?.pages.flatMap((p) => p.events) ?? [];

  return (
    <div className="card p-4">
      <div className="flex items-center mb-3">
        <ActivityIcon className="w-4 h-4 mr-2 text-muted" />
        <h3 className="text-md font-semibold">Activity</h3>
      </div>

      {isLoading ? (
        <div className="text-muted text-sm">Loading…</div>
      ) : allEvents.length === 0 ? (
        <div className="text-muted text-sm">No activity yet.</div>
      ) : (
        <>
          <ul className="space-y-2">
            {allEvents.map((e, i) => (
              <li key={i} className="flex items-start gap-3 text-sm">
                <div className="mt-0.5 shrink-0 w-6 h-6 rounded-full bg-panel2 flex items-center justify-center">
                  {iconFor(e)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate">{activitySummary(e)}</div>
                  {e.comments && (
                    <div className="text-xs text-muted truncate">{e.comments}</div>
                  )}
                </div>
                <div className="text-xs text-muted whitespace-nowrap shrink-0">
                  {e.user?.name ?? "system"} <span className="px-1">·</span>
                  <span title={formatDateTime(e.occurred_at)}>
                    {relativeTime(e.occurred_at)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
          {hasNextPage && (
            <div className="mt-3 flex justify-center">
              <button
                className="btn text-xs"
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
              >
                {isFetchingNextPage ? "Loading…" : "Load older"}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
