import { Link } from "react-router-dom";
import { FolderKanban, RefreshCw } from "lucide-react";
import EmptyState from "@/components/EmptyState";
import type { SourcingBomResponse } from "./sourcingTypes";

export function SourcingSkeleton() {
  return (
    <div className="space-y-3" role="status" aria-label="Loading sourced BOM">
      {[0, 1, 2].map(section => (
        <div key={section} className="card p-4 animate-pulse">
          <div className="h-4 w-48 rounded bg-panel2 mb-4" />
          <div className="space-y-2">
            {[0, 1, 2].map(row => (
              <div key={row} className="grid grid-cols-5 gap-3">
                {[0, 1, 2, 3, 4].map(cell => (
                  <div key={cell} className="h-3 rounded bg-panel2" />
                ))}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function EmptyBomState({ projectId }: { projectId: string }) {
  return (
    <EmptyState
      icon={FolderKanban}
      title="BOM is empty"
      description="Add BOM lines first to run sourcing coverage."
      action={{ label: "Add BOM lines first", to: `/projects/${projectId}/import` }}
    />
  );
}

export function SourcingDiagnosticsPanel({
  data,
  projectId,
  status,
  onRefresh,
}: {
  data?: SourcingBomResponse;
  projectId: string;
  status: number | null;
  onRefresh: () => void;
}) {
  if (status === 409) {
    return (
      <div className="card p-4 space-y-3" role="status" aria-label="Sourcing diagnostics">
        <div>
          <div className="font-medium">Sourcing not configured.</div>
          <div className="text-sm text-muted">
            Sourcing cannot run until TrustedParts credentials and workspace defaults are configured.
          </div>
        </div>
        <Link className="btn" to="/settings/workspace">
          Open Settings → Sourcing
        </Link>
      </div>
    );
  }

  const rows = data?.rows ?? [];
  if (rows.length === 0 || rows.some(row => row.best_offer)) {
    return null;
  }

  const allNoMpn = rows.every(row => row.reason === "no_mpn" || !row.mpn);
  const allCacheHit = rows.every(row => row.cache_hit === true);
  const fxUnavailable = rows.some(row => row.fx_status === "unavailable");

  let title = "No matching offers found.";
  let description = "TrustedParts returned no authorized offers for the selected country, currency, and distributors.";

  if (allNoMpn) {
    title = "BOM lines need manufacturer part numbers.";
    description = "Add MPNs to these parts, then source the BOM again.";
  } else if (fxUnavailable) {
    title = "Prices were found, but currency conversion is unavailable.";
    description = "Retry later or choose the offer currency while exchange rates are unavailable.";
  } else if (allCacheHit) {
    title = "Only cached no-offer results were available.";
    description = "Refresh prices to check TrustedParts again for the current sourcing filters.";
  }

  return (
    <div className="card p-4 space-y-3" role="status" aria-label="Sourcing diagnostics">
      <div>
        <div className="font-medium">{title}</div>
        <div className="text-sm text-muted">{description}</div>
      </div>
      {allCacheHit && (
        <button type="button" className="btn" onClick={onRefresh}>
          <RefreshCw size={14} aria-hidden="true" />
          Refresh prices
        </button>
      )}
      {allNoMpn && (
        <Link className="btn" to={`/projects/${projectId}/import`}>
          Edit BOM
        </Link>
      )}
    </div>
  );
}

export function BudgetState({
  disabledUntil,
  onRetry,
}: {
  disabledUntil: number | null;
  onRetry: () => void;
}) {
  const disabled = disabledUntil != null && Date.now() < disabledUntil;
  return (
    <div className="card p-4 text-sm text-muted" role="status">
      <div>TrustedParts request budget reached for this hour. Retry is paused for 5 minutes.</div>
      <button type="button" className="btn mt-3" disabled={disabled} onClick={onRetry}>
        Retry Source BOM
      </button>
    </div>
  );
}
