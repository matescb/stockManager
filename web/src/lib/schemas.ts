/**
 * Zod schemas for API response shapes.
 *
 * The 2026-04-30 review's FE HIGH-2 flagged that `body?.data as T` in
 * `lib/api.ts` returns whatever the server sent without runtime
 * validation — server-side endpoint changes silently break the UI.
 * This module is the foundation for fixing that: each schema mirrors
 * its TypeScript counterpart in `types.ts`, and `api.parsed*` (in
 * lib/api.ts) parses responses through these schemas at the boundary.
 *
 * Migration is opt-in: callers that haven't switched still get the
 * legacy `as T` cast. New code and security-sensitive paths should
 * adopt `api.parsed*`.
 *
 * Maintenance rule: when a backend Pydantic schema changes, update the
 * matching schema here. Drift between the two surfaces as a parse
 * error in the running app, not a silent UI break days later.
 *
 * Zod default behaviour:
 *  - Unknown fields on the backend response are stripped (we don't use
 *    .strict()). Forward-compatible — backend can add fields without
 *    breaking the frontend.
 *  - Missing required fields throw at parse time → ApiError surfaces
 *    a clear "schema mismatch" error.
 */
import { z } from "zod";

// ---------------------------------------------------------------------
// Atom-shaped helpers reused across resources.
// ---------------------------------------------------------------------

const uuid = z.string().uuid();
const isoDate = z.string();           // ISO 8601 string; not parsed to Date
const nullableString = z.string().nullable();
const nullableNumber = z.number().nullable();
const optionalNullableString = z.string().nullable().optional();
const optionalNullableUuid = uuid.nullable().optional();

// ---------------------------------------------------------------------
// Resource schemas. Mirror types.ts; export inferred types so consumers
// have a single source of truth (types.ts re-exports these).
// ---------------------------------------------------------------------

export const PartSchema = z.object({
  id: uuid,
  part_type: z.enum(["linked", "local", "meta", "sub_assembly"]),
  name: z.string(),
  manufacturer: nullableString,
  mpn: nullableString,
  internal_part_number: nullableString,
  description: nullableString,
  footprint: nullableString,
  notes_markdown: nullableString,
  low_stock_report_quantity: nullableNumber,
  attrition_percentage: z.number(),
  attrition_min_quantity: z.number(),
  default_storage_location_id: uuid.nullable(),
  default_storage_mandatory: z.boolean(),
  serialized: z.boolean(),
  // Optional rather than plain `.nullable()` for the same reason as
  // `published` below — the field was added after the schema shipped, and
  // marking it optional keeps older fixtures and any cached response
  // parseable instead of throwing a schema-mismatch at the boundary.
  category_id: optionalNullableUuid,
  published: z.boolean().optional(),
  linked_provider: z.enum(["mouser", "digikey"]).nullable(),
  linked_external_id: nullableString,
  last_refresh_at: nullableString,
  description_locally_edited: z.boolean(),
  archived_at: nullableString,
  on_hand: nullableNumber,
  reserved: z.number(),
  available: z.number(),
  image_url: nullableString,
});
export type Part = z.infer<typeof PartSchema>;

export const PartsListSchema = z.array(PartSchema);

export const PartCreateSchema = z.object({
  part_type: z.enum(["linked", "local", "meta", "sub_assembly"]).optional(),
  name: z.string().max(300).nullable().optional(),
  manufacturer: optionalNullableString,
  mpn: optionalNullableString,
  internal_part_number: optionalNullableString,
  description: optionalNullableString,
  notes_markdown: optionalNullableString,
  footprint: optionalNullableString,
  low_stock_report_quantity: z.number().int().nullable().optional(),
  attrition_percentage: z.number().optional(),
  attrition_min_quantity: z.number().int().optional(),
  default_storage_location_id: optionalNullableUuid,
  default_storage_mandatory: z.boolean().optional(),
  serialized: z.boolean().optional(),
  category_id: optionalNullableUuid,
}).strict();
export type PartCreate = z.infer<typeof PartCreateSchema>;

/**
 * A workspace-scoped bucket for parts. `library_slug` is the stable,
 * URL- and KiCad-library-safe identifier; the server derives it from
 * `name` when the caller doesn't supply one, and a rename never moves it.
 */
export const PartCategorySchema = z.object({
  id: uuid,
  name: z.string(),
  description: nullableString,
  sort_order: z.number(),
  refdes_prefix: nullableString,
  default_symbol_ref: nullableString,
  default_footprint_ref: nullableString,
  footprint_filters: z.array(z.string()).nullable(),
  library_slug: z.string(),
  archived_at: nullableString,
});
export type PartCategory = z.infer<typeof PartCategorySchema>;

export const PartCategoriesListSchema = z.array(PartCategorySchema);

// ---------------------------------------------------------------------
// EDA libraries — the workspace's KiCad symbols, footprints, 3D models
// and SPICE models, plus the per-part config naming which it uses.
// Backend: `app/domain/eda/schemas.py`.
// ---------------------------------------------------------------------

/** Shared by symbols and footprints — same columns, same lifecycle. */
const EdaEntryFields = {
  id: uuid,
  /** The KiCad entry name — the `Entry` half of a `LibNick:Entry` ref. */
  name: z.string(),
  /** Content hash; also the stored filename stem. The file is immutable. */
  sha256: z.string(),
  size_bytes: z.number(),
  /** Server-controlled: manual | snapeda | samacsys | ultralibrarian | easyeda. */
  source: z.string(),
  category_id: uuid.nullable(),
  archived_at: nullableString,
};

export const EdaSymbolSchema = z.object(EdaEntryFields);
export type EdaSymbol = z.infer<typeof EdaSymbolSchema>;
export const EdaSymbolsListSchema = z.array(EdaSymbolSchema);

export const EdaFootprintSchema = z.object(EdaEntryFields);
export type EdaFootprint = z.infer<typeof EdaFootprintSchema>;
export const EdaFootprintsListSchema = z.array(EdaFootprintSchema);

export const EdaDatafileSchema = z.object({
  id: uuid,
  /** step | wrl | spice — derived server-side from the upload's extension. */
  kind: z.enum(["step", "wrl", "spice"]),
  name: z.string(),
  sha256: z.string(),
  size_bytes: z.number(),
  source: z.string(),
  archived_at: nullableString,
});
export type EdaDatafile = z.infer<typeof EdaDatafileSchema>;
export const EdaDatafilesListSchema = z.array(EdaDatafileSchema);

/** A 3D model attached to a footprint, ordered by `position`. */
export const EdaFootprintModelSchema = z.object({
  datafile_id: uuid,
  position: z.number(),
});
export type EdaFootprintModel = z.infer<typeof EdaFootprintModelSchema>;
export const EdaFootprintModelsListSchema = z.array(EdaFootprintModelSchema);

/**
 * A part's EDA configuration.
 *
 * Each of the symbol and footprint slots is named EITHER by a hosted id
 * (`*_id`) or by a KiCad `LibNick:Entry` string into the user's own
 * libraries (`*_ref_external`) — never both; the server 422s on that.
 * Both null means "inherit the category default".
 */
export const PartEdaSchema = z.object({
  part_id: uuid,
  symbol_id: uuid.nullable(),
  symbol_ref_external: nullableString,
  footprint_id: uuid.nullable(),
  footprint_ref_external: nullableString,
  spice_datafile_id: uuid.nullable(),
  value: nullableString,
  keywords: nullableString,
  footprint_filters: z.array(z.string()).nullable(),
  exclude_from_bom: z.boolean(),
  exclude_from_board: z.boolean(),
  exclude_from_sim: z.boolean(),
  sim_device: nullableString,
  sim_pins: nullableString,
  sim_params: nullableString,
});
export type PartEda = z.infer<typeof PartEdaSchema>;

/**
 * The PUT body. A full replacement, not a merge — the server writes every
 * column from this payload, so an omitted field resets to its default.
 */
export const PartEdaWriteSchema = z.object({
  symbol_id: optionalNullableUuid,
  symbol_ref_external: optionalNullableString,
  footprint_id: optionalNullableUuid,
  footprint_ref_external: optionalNullableString,
  spice_datafile_id: optionalNullableUuid,
  value: optionalNullableString,
  keywords: optionalNullableString,
  footprint_filters: z.array(z.string()).nullable().optional(),
  exclude_from_bom: z.boolean().optional(),
  exclude_from_board: z.boolean().optional(),
  exclude_from_sim: z.boolean().optional(),
  sim_device: optionalNullableString,
  sim_pins: optionalNullableString,
  sim_params: optionalNullableString,
}).strict();
export type PartEdaWrite = z.infer<typeof PartEdaWriteSchema>;

/** Paged parts response — returned by GET /parts with cursor pagination. */
export const PagedPartsSchema = z.object({
  items: z.array(PartSchema),
  next_cursor: z.string().nullable(),
});
export type PagedParts = z.infer<typeof PagedPartsSchema>;

export const StorageLocationSchema = z.object({
  id: uuid,
  name: z.string(),
  description: nullableString,
  single_part_only: z.boolean(),
  existing_parts_only: z.boolean(),
  is_full: z.boolean(),
  archived_at: nullableString,
});
export type StorageLocation = z.infer<typeof StorageLocationSchema>;

export const StorageLocationsListSchema = z.array(StorageLocationSchema);

export const SpecSourceSchema = z.enum(["provider", "manual", "override"]);
export type SpecSource = z.infer<typeof SpecSourceSchema>;

export const CustomFieldRowSchema = z.object({
  id: uuid,
  key: z.string(),
  value: nullableString,
  source: SpecSourceSchema,
  original_value: nullableString,
});
export type CustomFieldRow = z.infer<typeof CustomFieldRowSchema>;

export const CustomFieldRowsListSchema = z.array(CustomFieldRowSchema);

export const LotSchema = z.object({
  id: uuid,
  part_id: uuid,
  name: nullableString,
  serial_number: nullableString,
  parent_lot_id: uuid.nullable(),
  description: nullableString,
  comments: nullableString,
  expiration_date: nullableString,
  source_type: z.string(),
  purchase_quantity: nullableNumber,
  purchase_unit_cost: nullableNumber,
  purchase_currency: nullableString,
  current_quantity: nullableNumber,
  created_at: isoDate,
});
export type Lot = z.infer<typeof LotSchema>;

export const LotsListSchema = z.array(LotSchema);

export const StockEntrySchema = z.object({
  id: uuid,
  part_id: uuid,
  lot_id: uuid.nullable(),
  storage_location_id: uuid.nullable(),
  quantity_delta: z.number(),
  status: z.string(),
  unit_price: nullableNumber,
  currency: nullableString,
  operation_type: z.string(),
  comments: nullableString,
  occurred_at: isoDate,
});
export type StockEntry = z.infer<typeof StockEntrySchema>;

const PriceInputSchema = z.object({
  mode: z.enum(["none", "per_component", "entire_lot"]).optional(),
  unit_price: z.number().nullable().optional(),
  total_price: z.number().nullable().optional(),
  currency: optionalNullableString,
}).strict();

const LotInputSchema = z.object({
  name: optionalNullableString,
  comments: optionalNullableString,
  expiration_date: optionalNullableString,
  serial_number: optionalNullableString,
}).strict();

export const PartAddStockSchema = z.object({
  part_id: uuid,
  quantity: z.number().int().gt(0),
  storage_location_id: optionalNullableUuid,
  price: PriceInputSchema.nullable().optional(),
  lot: LotInputSchema.nullable().optional(),
  comments: optionalNullableString,
  bag_signature: z.string().regex(/^[a-f0-9]{64}$/).nullable().optional(),
  raw_bag_code: z.string().max(4096).nullable().optional(),
}).strict();
export type PartAddStock = z.infer<typeof PartAddStockSchema>;

export const ProjectSchema = z.object({
  id: uuid,
  name: z.string(),
  description: nullableString,
  notes_markdown: nullableString,
  associated_subassembly_part_id: uuid.nullable(),
  archived_at: nullableString,
  created_at: isoDate,
  updated_at: isoDate,
});
export type Project = z.infer<typeof ProjectSchema>;

export const ProjectsListSchema = z.array(ProjectSchema);

export const OrderSchema = z.object({
  id: uuid,
  name: z.string(),
  order_type: z.enum(["purchase", "sales"]),
  supplier: nullableString,
  status: z.enum(["draft", "open", "partial", "received", "cancelled"]),
  ordered_on: nullableString,
  expected_on: nullableString,
  received_on: nullableString,
  currency: nullableString,
  comments: nullableString,
  archived_at: nullableString,
  totals: z.object({ ordered: z.number(), received: z.number() }),
  created_at: isoDate,
  updated_at: isoDate,
});
export type Order = z.infer<typeof OrderSchema>;

export const OrdersListSchema = z.array(OrderSchema);

export const OrderEntrySchema = z.object({
  id: uuid,
  order_id: uuid,
  part_id: uuid.nullable(),
  name: nullableString,
  quantity_ordered: z.number(),
  quantity_received: z.number(),
  unit_price: nullableNumber,
  currency: nullableString,
  comments: nullableString,
  order_index: z.number(),
});
export type OrderEntry = z.infer<typeof OrderEntrySchema>;

export const OrderReceiveSchema = z.object({
  received_on: optionalNullableString,
  lines: z.array(z.object({
    order_entry_id: uuid,
    quantity: z.number().int().gt(0),
    storage_location_id: optionalNullableUuid,
    lot_name: optionalNullableString,
    serial_number: optionalNullableString,
  }).strict()).min(1),
}).strict();
export type OrderReceive = z.infer<typeof OrderReceiveSchema>;

export const OrderReceiveResultSchema = z.object({
  order_id: uuid,
  status: z.enum(["draft", "open", "partial", "received", "cancelled"]),
  lots: z.array(uuid),
  stock_entries: z.array(uuid),
});
export type OrderReceiveResult = z.infer<typeof OrderReceiveResultSchema>;

export const BuildSchema = z.object({
  id: uuid,
  name: z.string(),
  project_id: uuid,
  quantity: z.number(),
  status: z.enum(["planned", "in_progress", "complete", "cancelled"]),
  started_at: nullableString,
  completed_at: nullableString,
  output_lot_id: uuid.nullable(),
  comments: nullableString,
  archived_at: nullableString,
  created_at: isoDate,
  updated_at: isoDate,
});
export type Build = z.infer<typeof BuildSchema>;

export const BuildsListSchema = z.array(BuildSchema);

export const ProjectEntrySchema = z.object({
  id: uuid,
  project_id: uuid,
  entry_type: z.enum(["part", "meta_part", "non_part", "unmatched"]),
  part_id: uuid.nullable(),
  meta_part_id: uuid.nullable(),
  name: nullableString,
  quantity: z.number(),
  comments: nullableString,
  designators: z.array(z.string()),
  cad_footprint: nullableString,
  cad_key: nullableString,
  dnp: z.boolean(),
  order_index: z.number(),
});
export type ProjectEntry = z.infer<typeof ProjectEntrySchema>;

// ---------------------------------------------------------------------
// Auth surface — load-bearing for the gate. Schema mismatch here
// breaks the login → workspace bootstrap, so it's the highest-value
// migration target.
// ---------------------------------------------------------------------

// `/api/auth/me` shape: matches the backend's response in
// app/api/routes/auth.py::me. The membership-status filter is applied
// server-side, so workspaces in the response are always active for
// this user.
export const MeWorkspaceSchema = z.object({
  id: uuid,
  name: z.string(),
  kind: z.string(),
});
export type MeWorkspace = z.infer<typeof MeWorkspaceSchema>;

export const MeSchema = z.object({
  user: z.object({
    id: uuid,
    email: z.string(),
    name: z.string(),
  }),
  workspaces: z.array(MeWorkspaceSchema),
});
export type Me = z.infer<typeof MeSchema>;
