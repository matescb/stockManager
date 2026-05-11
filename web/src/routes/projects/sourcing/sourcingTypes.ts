export type SourcingBomPriceBreak = {
  quantity: number;
  unit_price: string | number;
  currency?: string | null;
};

export type SourcingBomOffer = {
  mpn: string;
  distributor: string;
  sku?: string | null;
  stock: number;
  unit_price?: string | number | null;
  currency?: string | null;
  unit_price_converted?: string | number | null;
  currency_displayed?: string | null;
  fx_converted?: boolean | null;
  fx_rate_date?: string | null;
  packaging?: string | null;
  moq?: number | null;
  lead_time_days?: number | null;
  price_breaks?: SourcingBomPriceBreak[] | null;
  price_breaks_converted?: SourcingBomPriceBreak[] | null;
  url?: string | null;
  availability_text?: string | null;
  quantity_multiple?: number | null;
  lifecycle_risk?: string | null;
  supply_chain_risk?: string | null;
  is_affected_by_tariff?: boolean | null;
  rohs_compliance?: SourcingRohsCompliance[];
};

export type SourcingRohsCompliance = {
  region: string;
  is_compliant: boolean;
  description?: string | null;
};

export type RiskFlag =
  | "single_source"
  | "no_authorized_stock"
  | "moq_overbuy"
  | "lead_time_long"
  | "preferred_distributor_unmet"
  | "lifecycle_risk_present"
  | "supply_chain_risk_present"
  | "tariff_affected"
  | "rohs_non_compliant";

export type SourcingBomLine = {
  project_entry_id: string;
  part_id: string;
  part_name: string;
  mpn?: string | null;
  required: number;
  available: number;
  substitute_ids: string[];
  substitute_available: number;
  short_by: number;
  authorized_stock: number;
  offers: SourcingBomOffer[];
  best_offer?: SourcingBomOffer | null;
  est_extended_cost?: string | number | null;
  lead_time_days?: number | null;
  cache_hit?: boolean | null;
  reason?: "ok" | "no_mpn" | "no_offers" | null;
  fx_status?: "unavailable" | null;
  risk_flags: RiskFlag[];
};

export type CoverageRow = {
  distributor: string;
  lines_covered: number;
  lines_uncovered: string[];
  coverage_pct: number;
  est_total_cost?: string | number | null;
  worst_lead_time_days?: number | null;
};

export type SourcingBomResponse = {
  rows: SourcingBomLine[];
  coverage: {
    rows: CoverageRow[];
    total_lines: number;
    best_single_distributor?: string | null;
    best_two_distributor_combo?: [string, string] | null;
    lowest_total_price_combo: string[];
    lowest_total_price_total?: string | number | null;
    fewest_distributors_combo: string[];
    fewest_distributors_total?: string | number | null;
    target_coverage_pct: number;
  };
  capacity: {
    can_build_now: number;
    can_build_after_purchase: number;
    total_bom_cost?: string | number | null;
    cost_per_single_bom?: string | number | null;
    purchase_to_pay_cost?: string | number | null;
    est_purchase_cost?: string | number | null;
    blocking_lines_now: string[];
    blocking_lines_after_purchase: string[];
  };
  build_quantity: number;
  powered_by: "TrustedParts";
  fetched_at: string;
  partial: boolean;
  fx_status?: "ok" | "partial" | "unavailable" | null;
  links: {
    primary: string;
    attribution: string;
  };
};

export type SourcingRequest = {
  build_quantity: number;
  country?: string;
  currency?: string | null;
  distributors?: string[];
};
