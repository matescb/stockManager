import { useCallback, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, BellPlus, Pencil } from "lucide-react";
import { toast } from "sonner";
import { useConfirm } from "@/components/ConfirmDialog";
import { DataTable, type Column } from "@/components/DataTable";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import {
  alertTypeLabel,
  archiveSourcingAlert,
  isProjectAlert,
  listSourcingAlerts,
  type SourcingAlert,
  type SourcingAlertType,
} from "@/lib/sourcingAlerts";
import type { Part, Project } from "@/types";
import AlertFormModal from "./AlertFormModal";

type EnabledFilter = "all" | "enabled" | "disabled";

function formatThreshold(alert: SourcingAlert): string {
  switch (alert.alert_type) {
    case "stock_below":
      return `Below ${(alert.threshold as { qty?: number }).qty ?? 0}`;
    case "stock_above":
      return `Above ${(alert.threshold as { qty?: number }).qty ?? 0}`;
    case "price_changed":
      return `${(alert.threshold as { delta_pct?: number }).delta_pct ?? 0}% change`;
    case "bom_buyable":
      return `Build ${(alert.threshold as { build_quantity?: number }).build_quantity ?? 1}`;
    case "back_in_stock":
      return "Authorized stock returns";
    case "out_of_authorized_stock":
      return "Authorized stock reaches zero";
    case "lifecycle_risk_changed":
    case "supply_chain_risk_changed": {
      const threshold = alert.threshold as { must_contain?: string | null; case_sensitive?: boolean };
      return threshold.must_contain
        ? `New value contains ${threshold.must_contain}`
        : "Any value change";
    }
    case "tariff_status_changed":
      return "Any status change";
  }
}

function mapById<T extends { id: string }>(rows: T[] | undefined): Map<string, T> {
  return new Map((rows ?? []).map(row => [row.id, row]));
}

export default function AlertsPage() {
  const { workspaceId } = useAuth();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [typeFilter, setTypeFilter] = useState<SourcingAlertType | "">("");
  const [enabledFilter, setEnabledFilter] = useState<EnabledFilter>("all");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [formAlert, setFormAlert] = useState<SourcingAlert | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const enabled = enabledFilter === "all" ? null : enabledFilter === "enabled";
  const query = useQuery({
    queryKey: useWsKey("sourcing-alerts", typeFilter, enabledFilter, includeArchived),
    queryFn: ({ signal }) => listSourcingAlerts({
      alert_type: typeFilter,
      enabled,
      include_archived: includeArchived,
    }, { signal }),
  });
  const partsQuery = useQuery({
    queryKey: useWsKey("parts", "alert-scope-map"),
    queryFn: () => api.get<Part[]>("/parts?limit=200"),
  });
  const projectsQuery = useQuery({
    queryKey: useWsKey("projects", "alert-scope-map"),
    queryFn: () => api.get<Project[]>("/projects?limit=200"),
  });
  const partMap = useMemo(() => mapById(partsQuery.data), [partsQuery.data]);
  const projectMap = useMemo(() => mapById(projectsQuery.data), [projectsQuery.data]);
  const rows = query.data ?? [];

  const archiveMutation = useApiMutation<SourcingAlert, SourcingAlert>({
    mutationKey: wsKeyOf(workspaceId, "sourcing-alerts", "archive"),
    mutationFn: alert => archiveSourcingAlert(alert.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "sourcing-alerts") });
      toast.success("Alert archived.");
    },
    onError: error => {
      toast.error(error instanceof ApiError ? error.userMessage : "Failed to archive alert.");
    },
  });

  function invalidateAlerts() {
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "sourcing-alerts") });
  }

  const scopeLabel = useCallback((alert: SourcingAlert): string => {
    if (isProjectAlert(alert.alert_type)) {
      return projectMap.get(alert.project_id ?? "")?.name ?? alert.project_id ?? "Project";
    }
    return partMap.get(alert.part_id ?? "")?.name ?? alert.part_id ?? "Part";
  }, [partMap, projectMap]);

  const columns = useMemo<Column<SourcingAlert>[]>(() => [
    {
      key: "type",
      header: "Type",
      accessor: row => alertTypeLabel(row.alert_type),
      render: row => <span className="pill">{alertTypeLabel(row.alert_type)}</span>,
    },
    { key: "scope", header: "Scope", accessor: scopeLabel },
    { key: "threshold", header: "Threshold", accessor: formatThreshold },
    {
      key: "enabled",
      header: "Enabled",
      accessor: row => row.enabled,
      render: row => row.enabled ? <span className="pill bg-success/10 text-success">Enabled</span> : <span className="pill">Disabled</span>,
    },
    {
      key: "last_checked",
      header: "Last checked",
      accessor: row => row.last_checked_at ?? "",
      render: row => row.last_checked_at ? formatDateTime(row.last_checked_at) : "—",
    },
    {
      key: "last_notified",
      header: "Last notified",
      accessor: row => row.last_notified_at ?? "",
      render: row => row.last_notified_at ? formatDateTime(row.last_notified_at) : "—",
    },
    {
      key: "actions",
      header: "",
      render: row => (
        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              setFormAlert(row);
              setFormOpen(true);
            }}
          >
            <Pencil size={14} aria-hidden="true" />
            Edit
          </button>
          <button
            type="button"
            className="btn-danger btn-sm"
            disabled={archiveMutation.isPending || row.archived_at !== null}
            onClick={async () => {
              const ok = await confirm({
                title: "Archive alert?",
                message: "Archived alerts stop evaluating but remain visible when archived rows are included.",
                confirmLabel: "Archive",
                severity: "danger",
              });
              if (ok) archiveMutation.mutate(row);
            }}
          >
            <Archive size={14} aria-hidden="true" />
            Archive
          </button>
        </div>
      ),
    },
  ], [archiveMutation, confirm, scopeLabel]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm text-muted">Sourcing</div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold">Alerts</h1>
            <PoweredByTrustedParts />
          </div>
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => {
            setFormAlert(null);
            setFormOpen(true);
          }}
        >
          <BellPlus size={16} aria-hidden="true" />
          Create alert
        </button>
      </div>

      <div className="card p-3">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <label className="label">
            Alert type
            <select className="input" value={typeFilter} onChange={event => setTypeFilter(event.currentTarget.value as SourcingAlertType | "")}>
              <option value="">All types</option>
              <option value="stock_below">Stock below</option>
              <option value="stock_above">Stock above</option>
              <option value="back_in_stock">Back in stock</option>
              <option value="out_of_authorized_stock">Out of authorized stock</option>
              <option value="price_changed">Price changed</option>
              <option value="bom_buyable">BOM buyable</option>
            </select>
          </label>
          <label className="label">
            Enabled
            <select className="input" value={enabledFilter} onChange={event => setEnabledFilter(event.currentTarget.value as EnabledFilter)}>
              <option value="all">All</option>
              <option value="enabled">Enabled only</option>
              <option value="disabled">Disabled only</option>
            </select>
          </label>
          <label className="label flex-row items-center gap-2 pt-6">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={event => setIncludeArchived(event.currentTarget.checked)}
            />
            Include archived
          </label>
        </div>
      </div>

      {query.isError && <InlineQueryError query={query} label="sourcing alerts" />}
      <DataTable
        tableId="sourcing-alerts"
        rows={rows}
        rowKey={row => row.id}
        columns={columns}
        searchPlaceholder="Search alerts..."
        exportFilename="sourcing-alerts"
        empty={query.isLoading ? "Loading alerts..." : "No sourcing alerts."}
      />

      <AlertFormModal
        open={formOpen}
        mode={formAlert ? "edit" : "create"}
        alert={formAlert}
        onClose={() => setFormOpen(false)}
        onSaved={invalidateAlerts}
      />
    </div>
  );
}
