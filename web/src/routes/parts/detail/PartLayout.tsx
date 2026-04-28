import { Outlet, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import EntityHeader from "@/components/EntityHeader";
import SubNav from "@/components/SubNav";
import type { Part } from "@/types";

export default function PartLayout() {
  const { partId } = useParams<{ partId: string }>();
  const { data: part } = useQuery({
    queryKey: ["part", partId],
    queryFn: () => api.get<Part>(`/parts/${partId}`),
    enabled: !!partId,
  });

  if (!part) return <div className="text-muted">Loading…</div>;
  const items = [
    { to: `/parts/${part.id}/info`, label: "Part info" },
    { to: `/parts/${part.id}/stock`, label: "Stock" },
    { to: `/parts/${part.id}/add`, label: "Add stock" },
    { to: `/parts/${part.id}/remove`, label: "Remove stock" },
    { to: `/parts/${part.id}/move`, label: "Move stock" },
    { to: `/parts/${part.id}/history`, label: "History" },
    { to: `/parts/${part.id}/lots`, label: "Lots" },
    { to: `/parts/${part.id}/substitutes`, label: "Substitutes" },
    { to: `/parts/${part.id}/settings`, label: "Settings" },
    { to: `/parts/${part.id}/other`, label: "Other" },
  ];
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
      />
      <SubNav items={items} />
      <Outlet context={{ part }} />
    </div>
  );
}
