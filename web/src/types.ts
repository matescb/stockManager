export type Part = {
  id: string;
  part_type: "linked" | "local" | "meta" | "sub_assembly";
  name: string;
  manufacturer: string | null;
  mpn: string | null;
  internal_part_number: string | null;
  description: string | null;
  footprint: string | null;
  notes_markdown: string | null;
  low_stock_report_quantity: number | null;
  attrition_percentage: number;
  attrition_min_quantity: number;
  default_storage_location_id: string | null;
  default_storage_mandatory: boolean;
  serialized: boolean;
  published?: boolean;
  linked_provider: "mouser" | null;
  linked_external_id: string | null;
  last_refresh_at: string | null;
  description_locally_edited: boolean;
  archived_at: string | null;
  on_hand: number | null;
  reserved: number;
  available: number;
};

export type SpecSource = "provider" | "manual" | "override";

export type CustomFieldRow = {
  id: string;
  key: string;
  value: string | null;
  source: SpecSource;
  original_value: string | null;
};

export type StorageLocation = {
  id: string;
  name: string;
  description: string | null;
  single_part_only: boolean;
  existing_parts_only: boolean;
  is_full: boolean;
  archived_at: string | null;
};

export type Lot = {
  id: string;
  part_id: string;
  name: string | null;
  serial_number: string | null;
  parent_lot_id: string | null;
  description: string | null;
  comments: string | null;
  expiration_date: string | null;
  source_type: string;
  purchase_quantity: number | null;
  purchase_unit_cost: number | null;
  purchase_currency: string | null;
  current_quantity: number | null;
  created_at: string;
};

export type StockEntry = {
  id: string;
  part_id: string;
  lot_id: string | null;
  storage_location_id: string | null;
  quantity_delta: number;
  status: string;
  unit_price: number | null;
  currency: string | null;
  operation_type: string;
  comments: string | null;
  occurred_at: string;
};

export type Project = {
  id: string;
  name: string;
  description: string | null;
  notes_markdown: string | null;
  associated_subassembly_part_id: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Order = {
  id: string;
  name: string;
  order_type: "purchase" | "sales";
  supplier: string | null;
  status: "draft" | "open" | "partial" | "received" | "cancelled";
  ordered_on: string | null;
  expected_on: string | null;
  received_on: string | null;
  currency: string | null;
  comments: string | null;
  archived_at: string | null;
  totals: { ordered: number; received: number };
  created_at: string;
  updated_at: string;
};

export type OrderEntry = {
  id: string;
  order_id: string;
  part_id: string | null;
  name: string | null;
  quantity_ordered: number;
  quantity_received: number;
  unit_price: number | null;
  currency: string | null;
  comments: string | null;
  order_index: number;
};

export type Build = {
  id: string;
  name: string;
  project_id: string;
  quantity: number;
  status: "planned" | "in_progress" | "complete" | "cancelled";
  started_at: string | null;
  completed_at: string | null;
  output_lot_id: string | null;
  comments: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
};

export type BuildShortageRow = {
  project_entry_id: string;
  part_id: string;
  part_name: string;
  required: number;
  available: number;
  substitute_ids: string[];
  substitute_available: number;
  short_by: number;
};

export type PartsProviderName = "none" | "mouser";

export type ProviderSpec = { key: string; value: string };

export type MpnLookupResult = {
  found: boolean;
  result: {
    mpn: string;
    manufacturer: string | null;
    description: string | null;
    category: string | null;
    footprint: string | null;
    datasheet_url: string | null;
    image_url: string | null;
    source_url: string;
    specs: ProviderSpec[];
  } | null;
  message: string | null;
  /** Which provider produced this response (or "none" when unconfigured). */
  provider: PartsProviderName;
};

export type ProjectEntry = {
  id: string;
  project_id: string;
  entry_type: "part" | "meta_part" | "non_part" | "unmatched";
  part_id: string | null;
  meta_part_id: string | null;
  name: string | null;
  quantity: number;
  comments: string | null;
  designators: string[];
  cad_footprint: string | null;
  cad_key: string | null;
  dnp: boolean;
  order_index: number;
};
