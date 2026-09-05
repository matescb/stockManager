import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bell, Loader2, X } from "lucide-react";
import { Modal } from "@/components/Modal";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import {
  ALERT_TYPES,
  alertTypeLabel,
  createSourcingAlert,
  isProjectAlert,
  isSourcingFilteredAlert,
  type SourcingAlert,
  type SourcingAlertInput,
  type SourcingAlertType,
  type WorkspaceMemberOption,
  type WorkspaceSourcingSettings,
  updateSourcingAlert,
} from "@/lib/sourcingAlerts";
import type { Part, Project } from "@/types";

export type AlertFormInitialValues = {
  alert_type?: SourcingAlertType;
  part_id?: string | null;
  project_id?: string | null;
  build_quantity?: number;
};

type Props = {
  open: boolean;
  mode?: "create" | "edit";
  alert?: SourcingAlert | null;
  initialValues?: AlertFormInitialValues;
  allowedTypes?: SourcingAlertType[];
  title?: string;
  onClose: () => void;
  onSaved?: (alert: SourcingAlert) => void;
};

const DEFAULT_COOLDOWN_SECONDS = 86400;
const EMPTY_THRESHOLD_TYPES = new Set<SourcingAlertType>([
  "back_in_stock",
  "out_of_authorized_stock",
  "tariff_status_changed",
]);
const STRING_CHANGED_TYPES = new Set<SourcingAlertType>([
  "lifecycle_risk_changed",
  "supply_chain_risk_changed",
]);

function positiveInt(value: string, fallback: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(fallback, Math.floor(parsed));
}

function thresholdFromAlert(alert: SourcingAlert | null | undefined, key: string, fallback: string): string {
  const value = alert?.threshold?.[key as keyof typeof alert.threshold];
  return value == null ? fallback : String(value);
}

function thresholdBoolFromAlert(alert: SourcingAlert | null | undefined, key: string): boolean {
  const value = alert?.threshold?.[key as keyof typeof alert.threshold];
  return value === true;
}

function defaultFromActiveList(saved: string | null | undefined, active: string[]): string {
  if (saved && active.includes(saved)) return saved;
  return active[0] ?? "";
}

function memberLabel(member: WorkspaceMemberOption): string {
  return member.name ? `${member.name} (${member.email})` : member.email;
}

function partSubtitle(part: Part): string {
  return [part.mpn, part.manufacturer].filter(Boolean).join(" - ");
}

export default function AlertFormModal({
  open,
  mode = "create",
  alert = null,
  initialValues,
  allowedTypes = [...ALERT_TYPES],
  title,
  onClose,
  onSaved,
}: Props) {
  const { workspaceId } = useAuth();
  const isEdit = mode === "edit" && alert !== null;
  const initialType = initialValues?.alert_type ?? alert?.alert_type ?? allowedTypes[0] ?? "stock_below";
  const [alertType, setAlertType] = useState<SourcingAlertType>(initialType);
  const [enabled, setEnabled] = useState(alert?.enabled ?? true);
  const [cooldownSeconds, setCooldownSeconds] = useState(String(alert?.cooldown_seconds ?? DEFAULT_COOLDOWN_SECONDS));
  const [notifyUserIds, setNotifyUserIds] = useState<string[]>(alert?.notify_user_ids ?? []);
  const [partId, setPartId] = useState(alert?.part_id ?? initialValues?.part_id ?? "");
  const [projectId, setProjectId] = useState(alert?.project_id ?? initialValues?.project_id ?? "");
  const [qty, setQty] = useState(thresholdFromAlert(alert, "qty", "0"));
  const [deltaPct, setDeltaPct] = useState(thresholdFromAlert(alert, "delta_pct", "5"));
  const [buildQuantity, setBuildQuantity] = useState(
    String(initialValues?.build_quantity ?? thresholdFromAlert(alert, "build_quantity", "1")),
  );
  const [mustContain, setMustContain] = useState(thresholdFromAlert(alert, "must_contain", ""));
  const [caseSensitive, setCaseSensitive] = useState(thresholdBoolFromAlert(alert, "case_sensitive"));
  const [countryCode, setCountryCode] = useState(alert?.country_code ?? "");
  const [currencyCode, setCurrencyCode] = useState(alert?.currency_code ?? "");
  const [distributorFilter, setDistributorFilter] = useState<string[]>(alert?.distributor_filter ?? []);
  const [partSearch, setPartSearch] = useState("");
  const [debouncedPartSearch, setDebouncedPartSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const nextType = initialValues?.alert_type ?? alert?.alert_type ?? allowedTypes[0] ?? "stock_below";
    setAlertType(nextType);
    setEnabled(alert?.enabled ?? true);
    setCooldownSeconds(String(alert?.cooldown_seconds ?? DEFAULT_COOLDOWN_SECONDS));
    setNotifyUserIds(alert?.notify_user_ids ?? []);
    setPartId(alert?.part_id ?? initialValues?.part_id ?? "");
    setProjectId(alert?.project_id ?? initialValues?.project_id ?? "");
    setQty(thresholdFromAlert(alert, "qty", "0"));
    setDeltaPct(thresholdFromAlert(alert, "delta_pct", "5"));
    setBuildQuantity(String(initialValues?.build_quantity ?? thresholdFromAlert(alert, "build_quantity", "1")));
    setMustContain(thresholdFromAlert(alert, "must_contain", ""));
    setCaseSensitive(thresholdBoolFromAlert(alert, "case_sensitive"));
    setCountryCode(alert?.country_code ?? "");
    setCurrencyCode(alert?.currency_code ?? "");
    setDistributorFilter(alert?.distributor_filter ?? []);
    setPartSearch("");
    setError(null);
  }, [open, alert, initialValues, allowedTypes]);

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedPartSearch(partSearch.trim()), 250);
    return () => window.clearTimeout(handle);
  }, [partSearch]);

  const workspaceQuery = useQuery({
    queryKey: useWsKey("ws", "current"),
    queryFn: ({ signal }) => api.get<WorkspaceSourcingSettings>("/workspaces/current", { signal }),
    enabled: open,
  });
  const membersQuery = useQuery({
    queryKey: useWsKey("ws", "members"),
    queryFn: ({ signal }) => api.get<WorkspaceMemberOption[]>("/workspaces/members", { signal }),
    enabled: open,
  });
  const partsQuery = useQuery({
    queryKey: useWsKey("parts", "alert-picker", debouncedPartSearch),
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({ limit: "20" });
      if (debouncedPartSearch) {
        params.set("q", debouncedPartSearch);
        params.set("search", debouncedPartSearch);
      }
      return api.get<Part[]>(`/parts?${params.toString()}`, { signal });
    },
    enabled: open && !isProjectAlert(alertType),
  });
  const projectsQuery = useQuery({
    queryKey: useWsKey("projects", "alert-picker"),
    queryFn: ({ signal }) => api.get<Project[]>("/projects?limit=200", { signal }),
    enabled: open && isProjectAlert(alertType),
  });

  const activeMembers = useMemo(
    () => (membersQuery.data ?? []).filter(member => member.status === "active"),
    [membersQuery.data],
  );
  const parts = partsQuery.data ?? [];
  const projects = projectsQuery.data ?? [];
  const workspace = workspaceQuery.data;
  const availableTypes = allowedTypes.length > 0 ? allowedTypes : [...ALERT_TYPES];
  const showSourcingFilters = isSourcingFilteredAlert(alertType);

  useEffect(() => {
    if (!open || !workspace || isEdit || !showSourcingFilters) return;
    const activeCountries = workspace.active_countries ?? [];
    const activeCurrencies = workspace.active_currencies ?? [];
    const activeDistributors = workspace.active_distributors ?? [];
    setCountryCode(current => current || defaultFromActiveList(workspace.sourcing_country_code, activeCountries));
    setCurrencyCode(current => current || defaultFromActiveList(workspace.sourcing_currency_code, activeCurrencies));
    setDistributorFilter(current => current.length > 0 ? current : (workspace.sourcing_preferred_distributors ?? []).filter(
      distributor => activeDistributors.includes(distributor),
    ));
  }, [open, workspace, isEdit, showSourcingFilters]);

  const mutation = useApiMutation<SourcingAlert, SourcingAlertInput>({
    mutationKey: wsKeyOf(workspaceId, "sourcing-alerts", isEdit ? alert?.id : "create"),
    mutationFn: payload => isEdit && alert
      ? updateSourcingAlert(alert.id, payload)
      : createSourcingAlert(payload),
    onSuccess: saved => {
      onSaved?.(saved);
      onClose();
    },
    onError: err => {
      setError(err instanceof ApiError ? err.userMessage : "Failed to save alert.");
    },
  });

  // A dismiss must not strand an in-flight save, so the focus-trap's own exits
  // (Escape, backdrop) obey the same guard the Cancel button does.
  const closeUnlessBusy = useCallback(() => {
    if (!mutation.isPending) onClose();
  }, [mutation.isPending, onClose]);

  if (!open) return null;

  function buildPayload(): SourcingAlertInput | null {
    const cooldown = Number(cooldownSeconds);
    if (!Number.isFinite(cooldown) || cooldown < 60) {
      setError("Cooldown must be at least 60 seconds.");
      return null;
    }
    if (!isProjectAlert(alertType) && !partId) {
      setError("Choose a part for this alert.");
      return null;
    }
    if (isProjectAlert(alertType) && !projectId) {
      setError("Choose a project for this alert.");
      return null;
    }

    let threshold: SourcingAlertInput["threshold"] = {};
    if (alertType === "stock_below" || alertType === "stock_above") {
      const parsedQty = Number(qty);
      if (!Number.isInteger(parsedQty) || parsedQty < 0) {
        setError("Quantity must be an integer of 0 or more.");
        return null;
      }
      threshold = { qty: parsedQty };
    } else if (alertType === "price_changed") {
      const parsedDelta = Number(deltaPct);
      if (!Number.isFinite(parsedDelta) || parsedDelta < 0.1 || parsedDelta > 100) {
        setError("Price-change percentage must be between 0.1 and 100.");
        return null;
      }
      threshold = { delta_pct: parsedDelta };
    } else if (alertType === "bom_buyable") {
      const parsedBuild = Number(buildQuantity);
      if (!Number.isInteger(parsedBuild) || parsedBuild < 1) {
        setError("Build quantity must be an integer of 1 or more.");
        return null;
      }
      threshold = { build_quantity: parsedBuild };
    } else if (STRING_CHANGED_TYPES.has(alertType)) {
      threshold = {
        must_contain: mustContain.trim() || null,
        case_sensitive: caseSensitive,
      };
    }

    const payload: SourcingAlertInput = {
      alert_type: alertType,
      part_id: isProjectAlert(alertType) ? null : partId,
      project_id: isProjectAlert(alertType) ? projectId : null,
      threshold,
      cooldown_seconds: Math.floor(cooldown),
      enabled,
      notify_user_ids: notifyUserIds.length > 0 ? notifyUserIds : null,
      country_code: null,
      currency_code: null,
      distributor_filter: null,
    };

    if (showSourcingFilters) {
      payload.country_code = countryCode || null;
      payload.currency_code = currencyCode || null;
      payload.distributor_filter = distributorFilter.length > 0 ? distributorFilter : null;
    }
    return payload;
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const payload = buildPayload();
    if (!payload) return;
    mutation.mutate(payload);
  }

  return (
    <Modal
      open={open}
      onClose={closeUnlessBusy}
      title={title ?? (isEdit ? "Edit alert" : "Create alert")}
      className="card w-full max-w-3xl shadow-lg"
    >
      <form className="p-4" noValidate onSubmit={submit}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Bell size={18} aria-hidden="true" />
              <h2 className="card-title text-text">
                {title ?? (isEdit ? "Edit alert" : "Create alert")}
              </h2>
            </div>
            <p className="mt-1 text-sm text-muted">
              {isEdit ? "Update the threshold, recipients, and active state." : "Choose when this workspace should be notified."}
            </p>
          </div>
          <button type="button" className="btn-ghost btn-sm" onClick={onClose} disabled={mutation.isPending} aria-label="Close alert form">
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        {error && <div className="mt-3 card p-3 text-sm text-danger" role="alert">{error}</div>}

        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
          <label className="label">
            Alert type
            <select
              className="input"
              value={alertType}
              disabled={mutation.isPending || isEdit}
              onChange={event => {
                const next = event.currentTarget.value as SourcingAlertType;
                setAlertType(next);
                if (isProjectAlert(next)) setPartId("");
                else setProjectId("");
              }}
            >
              {availableTypes.map(type => (
                <option key={type} value={type}>{alertTypeLabel(type)}</option>
              ))}
            </select>
          </label>
          <label className="label">
            Cooldown seconds
            <input
              className="input"
              type="number"
              min={60}
              step={60}
              inputMode="numeric"
              value={cooldownSeconds}
              onChange={event => setCooldownSeconds(event.currentTarget.value)}
              disabled={mutation.isPending}
            />
          </label>
          <label className="label flex-row items-center gap-2 pt-6">
            <input
              type="checkbox"
              checked={enabled}
              onChange={event => setEnabled(event.currentTarget.checked)}
              disabled={mutation.isPending}
            />
            Enabled
          </label>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          {!isProjectAlert(alertType) ? (
            <section className="space-y-2">
              <label className="label">
                Part
                <input
                  className="input"
                  placeholder="Search MPN or name..."
                  value={partSearch}
                  onChange={event => setPartSearch(event.currentTarget.value)}
                  disabled={mutation.isPending}
                />
              </label>
              <div className="max-h-56 overflow-auto rounded border border-border">
                {partsQuery.isLoading ? (
                  <div className="flex items-center gap-2 p-3 text-sm text-muted">
                    <Loader2 size={14} className="animate-spin" /> Loading parts...
                  </div>
                ) : parts.length === 0 ? (
                  <div className="p-3 text-sm text-muted">No parts found.</div>
                ) : (
                  <div className="divide-y divide-border">
                    {parts.map(part => (
                      <label key={part.id} className="flex cursor-pointer items-center gap-3 p-3 hover:bg-panel2/40">
                        <input
                          type="radio"
                          name="alert-part"
                          checked={partId === part.id}
                          onChange={() => setPartId(part.id)}
                          disabled={mutation.isPending}
                        />
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium">{part.name}</span>
                          <span className="block truncate text-xs text-muted">{partSubtitle(part) || "No MPN"}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
              {partId && !parts.some(part => part.id === partId) && (
                <div className="text-xs text-muted">Selected part: {partId}</div>
              )}
            </section>
          ) : (
            <section className="space-y-2">
              <label className="label">
                Project
                <select
                  className="input"
                  value={projectId}
                  onChange={event => setProjectId(event.currentTarget.value)}
                  disabled={mutation.isPending || projectsQuery.isLoading}
                >
                  <option value="">Choose project...</option>
                  {projects.map(project => (
                    <option key={project.id} value={project.id}>{project.name}</option>
                  ))}
                </select>
              </label>
              {projectId && !projects.some(project => project.id === projectId) && (
                <div className="text-xs text-muted">Selected project: {projectId}</div>
              )}
            </section>
          )}

          <section className="space-y-3">
            {(alertType === "stock_below" || alertType === "stock_above") && (
              <label className="label">
                Quantity
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={1}
                  inputMode="numeric"
                  value={qty}
                  onChange={event => setQty(event.currentTarget.value)}
                  disabled={mutation.isPending}
                />
              </label>
            )}
            {alertType === "price_changed" && (
              <label className="label">
                Delta percent
                <input
                  className="input"
                  type="number"
                  min={0.1}
                  max={100}
                  step={0.1}
                  inputMode="decimal"
                  value={deltaPct}
                  onChange={event => setDeltaPct(event.currentTarget.value)}
                  disabled={mutation.isPending}
                />
              </label>
            )}
            {alertType === "bom_buyable" && (
              <label className="label">
                Build quantity
                <input
                  className="input"
                  type="number"
                  min={1}
                  step={1}
                  inputMode="numeric"
                  value={buildQuantity}
                  onChange={event => setBuildQuantity(event.currentTarget.value)}
                  onBlur={() => setBuildQuantity(String(positiveInt(buildQuantity, 1)))}
                  disabled={mutation.isPending}
                />
              </label>
            )}
            {STRING_CHANGED_TYPES.has(alertType) && (
              <div className="space-y-3">
                <label className="label">
                  Must contain
                  <input
                    className="input"
                    value={mustContain}
                    onChange={event => setMustContain(event.currentTarget.value)}
                    disabled={mutation.isPending}
                  />
                </label>
                <label className="label flex-row items-center gap-2">
                  <input
                    type="checkbox"
                    checked={caseSensitive}
                    onChange={event => setCaseSensitive(event.currentTarget.checked)}
                    disabled={mutation.isPending}
                  />
                  Case sensitive
                </label>
              </div>
            )}
            {EMPTY_THRESHOLD_TYPES.has(alertType) && (
              <div className="rounded border border-border p-3 text-sm text-muted">
                {alertType === "tariff_status_changed"
                  ? "This alert triggers on any tariff status transition; no threshold is needed."
                  : "This alert triggers on an availability transition; no numeric threshold is needed."}
              </div>
            )}
            <label className="label">
              Recipients
              <select
                multiple
                className="input min-h-32"
                value={notifyUserIds}
                onChange={event => {
                  setNotifyUserIds(Array.from(event.currentTarget.selectedOptions).map(option => option.value));
                }}
                disabled={mutation.isPending || membersQuery.isLoading}
              >
                {activeMembers.map(member => (
                  <option key={member.user_id} value={member.user_id}>{memberLabel(member)}</option>
                ))}
              </select>
            </label>
            <div className="text-xs text-muted">Leave recipients empty to notify workspace admins.</div>
          </section>
        </div>

        {showSourcingFilters && (
          <fieldset className="mt-4 border-t border-border pt-4">
            <legend className="section-title">Sourcing filters</legend>
            <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-3">
              <label className="label">
                Country
                <select
                  className="input uppercase"
                  value={countryCode}
                  onChange={event => setCountryCode(event.currentTarget.value)}
                  disabled={mutation.isPending || workspaceQuery.isLoading}
                >
                  <option value="">Workspace default</option>
                  {(workspace?.active_countries ?? []).map(item => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
              <label className="label">
                Currency
                <select
                  className="input uppercase"
                  value={currencyCode}
                  onChange={event => setCurrencyCode(event.currentTarget.value)}
                  disabled={mutation.isPending || workspaceQuery.isLoading}
                >
                  <option value="">Workspace default</option>
                  {(workspace?.active_currencies ?? []).map(item => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
              <label className="label">
                Distributor filter
                <select
                  multiple
                  className="input min-h-32"
                  value={distributorFilter}
                  onChange={event => setDistributorFilter(Array.from(event.currentTarget.selectedOptions).map(option => option.value))}
                  disabled={mutation.isPending || workspaceQuery.isLoading}
                >
                  {(workspace?.active_distributors ?? []).map(item => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
            </div>
          </fieldset>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose} disabled={mutation.isPending}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={mutation.isPending}>
            {mutation.isPending ? "Saving..." : isEdit ? "Save alert" : "Create alert"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
