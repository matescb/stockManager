import { api, type ApiOptions } from "@/lib/api";

export const ALERT_TYPES = [
  "stock_below",
  "stock_above",
  "back_in_stock",
  "out_of_authorized_stock",
  "price_changed",
  "bom_buyable",
  "lifecycle_risk_changed",
  "supply_chain_risk_changed",
  "tariff_status_changed",
] as const;

export type SourcingAlertType = typeof ALERT_TYPES[number];

export type SourcingAlertThreshold =
  | { qty: number }
  | { delta_pct: number }
  | { build_quantity: number }
  | { must_contain?: string | null; case_sensitive?: boolean }
  | Record<string, never>;

export type SourcingAlert = {
  id: string;
  workspace_id: string;
  alert_type: SourcingAlertType;
  part_id: string | null;
  project_id: string | null;
  threshold: SourcingAlertThreshold;
  country_code: string | null;
  currency_code: string | null;
  distributor_filter: string[] | null;
  notify_user_ids: string[] | null;
  cooldown_seconds: number;
  enabled: boolean;
  last_checked_at: string | null;
  last_notified_at: string | null;
  archived_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

export type SourcingAlertInput = {
  alert_type: SourcingAlertType;
  part_id?: string | null;
  project_id?: string | null;
  threshold: SourcingAlertThreshold;
  country_code?: string | null;
  currency_code?: string | null;
  distributor_filter?: string[] | null;
  notify_user_ids?: string[] | null;
  cooldown_seconds: number;
  enabled: boolean;
};

export type SourcingAlertFilters = {
  alert_type?: SourcingAlertType | "";
  enabled?: boolean | null;
  include_archived?: boolean;
  part_id?: string | null;
  project_id?: string | null;
};

export type WorkspaceSourcingSettings = {
  sourcing_country_code?: string | null;
  sourcing_currency_code?: string | null;
  sourcing_preferred_distributors?: string[] | null;
  active_countries?: string[];
  active_currencies?: string[];
  active_distributors?: string[];
};

export type WorkspaceMemberOption = {
  id: string;
  user_id: string;
  email: string;
  name: string;
  role: "owner" | "admin" | "member" | "viewer";
  status: "active" | "invited" | "disabled";
};

export function alertTypeLabel(type: SourcingAlertType): string {
  switch (type) {
    case "stock_below":
      return "Stock below";
    case "stock_above":
      return "Stock above";
    case "back_in_stock":
      return "Back in stock";
    case "out_of_authorized_stock":
      return "Out of authorized stock";
    case "price_changed":
      return "Price changed";
    case "bom_buyable":
      return "BOM buyable";
    case "lifecycle_risk_changed":
      return "Lifecycle risk changed";
    case "supply_chain_risk_changed":
      return "Supply-chain risk changed";
    case "tariff_status_changed":
      return "Tariff status changed";
  }
}

export function isProjectAlert(type: SourcingAlertType): boolean {
  return type === "bom_buyable";
}

export function isSourcingFilteredAlert(type: SourcingAlertType): boolean {
  return type === "back_in_stock"
    || type === "out_of_authorized_stock"
    || type === "price_changed"
    || type === "lifecycle_risk_changed"
    || type === "supply_chain_risk_changed"
    || type === "tariff_status_changed";
}

export function listSourcingAlerts(filters: SourcingAlertFilters = {}, opts?: ApiOptions) {
  const params = new URLSearchParams();
  if (filters.alert_type) params.set("alert_type", filters.alert_type);
  if (filters.enabled !== undefined && filters.enabled !== null) {
    params.set("enabled", String(filters.enabled));
  }
  if (filters.include_archived) params.set("include_archived", "true");
  if (filters.part_id) params.set("part_id", filters.part_id);
  if (filters.project_id) params.set("project_id", filters.project_id);
  const suffix = params.toString();
  return api.get<SourcingAlert[]>(`/sourcing/alerts${suffix ? `?${suffix}` : ""}`, opts);
}

export function createSourcingAlert(payload: SourcingAlertInput) {
  return api.post<SourcingAlert, SourcingAlertInput>("/sourcing/alerts", payload);
}

export function updateSourcingAlert(id: string, payload: SourcingAlertInput) {
  return api.patch<SourcingAlert, SourcingAlertInput>(`/sourcing/alerts/${id}`, payload);
}

export function archiveSourcingAlert(id: string) {
  return api.delete<SourcingAlert>(`/sourcing/alerts/${id}`);
}
