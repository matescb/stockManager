export type PurchasePlanLine = {
  id: string;
  project_entry_id?: string | null;
  part_id: string;
  mpn_searched: string;
  required_qty: number;
  internal_available_qty: number;
  shortage_qty: number;
  selected_distributor?: string | null;
  selected_qty?: number | null;
  selected_unit_price?: string | number | null;
  selected_currency?: string | null;
  selected_packaging?: string | null;
  selected_moq?: number | null;
  selected_lead_time_days?: number | null;
  selected_url?: string | null;
  risk_flags: string[];
};

export type PurchasePlan = {
  id: string;
  project_id: string;
  build_quantity: number;
  strategy: string;
  country_code?: string | null;
  currency_code?: string | null;
  preferred_distributors?: string[] | null;
  max_distributors?: number | null;
  moq_overbuy_cap?: number | null;
  price_tolerance_pct?: string | number | null;
  status: string;
  created_at: string;
  expires_at: string;
  last_refreshed_at?: string | null;
  lines: PurchasePlanLine[];
  distributors_used: string[];
  est_total_cost?: string | number | null;
  worst_lead_time_days?: number | null;
  unfilled_count: number;
};

export type PurchasePlanRequest = {
  build_quantity: number;
  strategy: string;
  country?: string;
  currency?: string;
  distributors?: string[];
  max_distributors?: number;
  moq_overbuy_cap?: number;
  price_tolerance_pct?: string;
};

export type CreatedOrder = {
  id: string;
  name: string;
  supplier?: string | null;
  status: string;
  currency?: string | null;
  comments?: string | null;
  entries: {
    id: string;
    part_id?: string | null;
    quantity_ordered: number;
    unit_price?: string | number | null;
    currency?: string | null;
    comments?: string | null;
  }[];
};

export type ConvertOrdersResponse = {
  orders: CreatedOrder[];
};
