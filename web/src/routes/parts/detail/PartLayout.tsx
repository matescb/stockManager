import { Outlet, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { formatQuantity } from "@/lib/format";
import EntityHeader from "@/components/EntityHeader";
import SubNav, { type SubNavEntry } from "@/components/SubNav";
import PrintLabelButton from "@/routes/labels/PrintLabelButton";
import type { Part } from "@/types";

/**
 * The part-detail tab strip.
 *
 * A part has up to 17 sub-routes. Rendered flat they overflowed the strip and
 * the last six were only reachable by horizontal scrolling — effectively
 * invisible. Nothing here is new or removed: every route below is the same URL
 * it always was, just reached through a grouped slot when it isn't a
 * first-class destination. Exported so a test can assert the full target set.
 */
export function partSubNavEntries(part: Part): SubNavEntry[] {
  const base = `/parts/${part.id}`;
  return [
    { to: `${base}/info`, label: "Part info" },
    { to: `${base}/specs`, label: "Specs" },
    // Sourcing only makes sense for parts some provider actually knows —
    // it surfaces stock/price/lead-time/etc. `linked_provider` is the
    // primary; `provider_links` also covers a part linked to a secondary
    // provider only, which has no primary link to show.
    ...(part.linked_provider || (part.provider_links?.length ?? 0) > 0
      ? [{ to: `${base}/sourcing`, label: "Sourcing" }]
      : []),
    { to: `${base}/cad`, label: "CAD" },
    { to: `${base}/stock`, label: "Stock" },
    {
      label: "Stock actions",
      items: [
        { to: `${base}/add`, label: "Add stock" },
        { to: `${base}/remove`, label: "Remove stock" },
        { to: `${base}/move`, label: "Move stock" },
      ],
    },
    { to: `${base}/history`, label: "History" },
    {
      label: "More",
      items: [
        { to: `${base}/authorized-supply`, label: "Authorized supply" },
        { to: `${base}/lots`, label: "Lots" },
        { to: `${base}/substitutes`, label: "Substitutes" },
        ...(part.part_type === "meta" ? [{ to: `${base}/members`, label: "Members" }] : []),
        { to: `${base}/attachments`, label: "Attachments" },
        { to: `${base}/activity`, label: "Activity" },
        { to: `${base}/settings`, label: "Settings" },
        { to: `${base}/other`, label: "Other" },
      ],
    },
  ];
}

export default function PartLayout() {
  const { partId } = useParams<{ partId: string }>();

  if (!partId) {
    return <div className="text-danger text-sm p-4">Missing part id.</div>;
  }

  return <PartLayoutQuery key={partId} partId={partId} />;
}

function PartLayoutQuery({ partId }: { partId: string }) {
  const { data: part, isError, error } = useQuery({
    queryKey: useWsKey("part", partId),
    queryFn: ({ signal }) => api.get<Part>(`/parts/${partId}`, { signal }),
  });

  if (isError) return <div className="text-danger text-sm p-4">Failed to load part. {error instanceof ApiError ? error.userMessage : ""}</div>;
  if (!part) return <div className="text-muted">Loading…</div>;
  const items = partSubNavEntries(part);
  const lowThreshold = part.low_stock_report_quantity;
  const onHand = part.on_hand ?? 0;
  const reserved = part.reserved ?? 0;
  const available = part.available ?? onHand - reserved;
  const stats: { label: string; value: string; tone?: "danger" | "warning" | "success" | "default" }[] = [
    {
      label: "On hand",
      value: formatQuantity(onHand),
      tone:
        lowThreshold != null
          ? onHand < lowThreshold
            ? "danger"
            : onHand < lowThreshold * 1.25
              ? "warning"
              : "success"
          : "default",
    },
  ];
  if (reserved > 0) {
    stats.push({ label: "Reserved", value: formatQuantity(reserved), tone: "warning" });
    stats.push({
      label: "Available",
      value: formatQuantity(available),
      tone:
        lowThreshold != null
          ? available < lowThreshold
            ? "warning"
            : "success"
          : "default",
    });
  }
  if (lowThreshold != null) stats.push({ label: "Threshold", value: formatQuantity(lowThreshold) });

  return (
    <div>
      <EntityHeader
        title={part.name}
        subtitle={
          <span>
            {part.manufacturer || "—"} {part.mpn && <span className="ml-2">{part.mpn}</span>}
            <span className="pill ml-2">{part.part_type}</span>
            {part.archived_at && <span className="pill ml-2 bg-danger/20 text-danger">archived</span>}
          </span>
        }
        idCode={part.id}
        stats={stats}
        imageUrl={part.image_url}
        actions={
          <PrintLabelButton entityType="part" entityId={part.id} entityName={part.name} />
        }
      />
      <SubNav items={items} />
      <Outlet key={part.id} context={{ part }} />
    </div>
  );
}
