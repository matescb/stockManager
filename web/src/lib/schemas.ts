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

/**
 * One row of `part_provider_links` — which providers know this part.
 * The primary appears here alongside every secondary; `linked_provider`
 * is what distinguishes it.
 */
export const ProviderLinkSchema = z.object({
  provider: z.string(),
  external_id: nullableString,
  source_url: nullableString,
  last_refresh_at: nullableString,
});
export type ProviderLink = z.infer<typeof ProviderLinkSchema>;

/**
 * One spec cell on a parts-list row: the display string and the SI
 * base-unit number behind it. `value_num` is a fixed-point STRING because
 * `NUMERIC(36,18)` is exact and a JS double is not — render `value`, sort
 * server-side, never compare `value_num` in JS.
 */
export const SpecColumnValueSchema = z.object({
  value: nullableString,
  value_num: nullableString,
});
export type SpecColumnValue = z.infer<typeof SpecColumnValueSchema>;

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
  // "Last change". Maintained by the server's `WorkspaceOwned` mixin, so
  // it is always sent; optional here for the same reason as `published`
  // below — a response cached before the field shipped must still parse.
  updated_at: optionalNullableString,
  description_locally_edited: z.boolean(),
  archived_at: nullableString,
  on_hand: nullableNumber,
  reserved: z.number(),
  available: z.number(),
  image_url: nullableString,
  // Present whenever the response actually loaded the link rows — part
  // detail, and (since the parts-list column work) part LISTS, which load
  // them in one batched query per page. Responses that echo a part
  // without touching the link table, such as create-part, omit the key
  // rather than send an empty array: `[]` means "looked, found none",
  // absent means "did not look". Optional so both still parse.
  provider_links: z.array(ProviderLinkSchema).optional(),
  // The per-category spec columns this request asked for
  // (`?spec_columns=resistance,tolerance`). Absent when it asked for none,
  // so the shape is unchanged for every other consumer of this endpoint.
  // Every requested key IS here even when the part has no value, which is
  // what lets a blank cell mean "no value" rather than "column dropped".
  specs: z.record(z.string(), SpecColumnValueSchema).optional(),
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

/** `{key, dir}` — a category's saved default sort for its parts listing. */
export const CategoryListSortSchema = z.object({
  key: z.string(),
  dir: z.enum(["asc", "desc"]),
});
export type CategoryListSort = z.infer<typeof CategoryListSortSchema>;

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
  // What a part in this category shows as its KiCad schematic `Value`,
  // rendered from the part's specs — "{resistance} {tolerance}
  // {package}". Null inherits from the nearest ancestor that sets one.
  value_template: nullableString,
  // Spec keys emitted as hidden KiCad symbol fields. Null inherits; an
  // empty array is an explicit "emit none".
  kicad_fields: z.array(z.string()).nullable(),
  // Which spec keys the parts list shows as columns when it is filtered to
  // this category, in order. Null inherits from the nearest ancestor that
  // sets one; `[]` is an explicit "no spec columns" that stops the walk.
  // The RESOLVED value (and where it was inherited from) comes from
  // `GET /api/categories/{id}/spec-schema`, not from here.
  list_columns: z.array(z.string()).nullable().optional().default(null),
  // That listing's default sort. Same null-vs-`[]` inheritance rule.
  list_sort: CategoryListSortSchema.nullable().optional().default(null),
  library_slug: z.string(),
  // Adjacency-list parent; null is a root of the tree. Cycles and depth
  // are the server's problem (`domain/categories/tree.py`), but
  // `lib/categoryTree.ts` still walks defensively — this data also
  // arrives from restored backups and the MCP surface.
  parent_id: uuid.nullable(),
  archived_at: nullableString,
});
export type PartCategory = z.infer<typeof PartCategorySchema>;

export const PartCategoriesListSchema = z.array(PartCategorySchema);

/** One canonical spec key a category's parts can carry. */
export const CategorySpecKeySchema = z.object({
  key: z.string(),
  label: z.string(),
  // SI base-unit symbol ("Ω", "F", "%"), or null for a value that is not
  // a quantity — a package code, a dielectric name.
  unit: nullableString,
  mandatory: z.boolean(),
  // A display hint: numeric keys right-align. NOT a promise that the sort
  // is numeric — a unitless count has no `value_num` and sorts as text.
  numeric: z.boolean(),
  // True for the keys every category carries, so the picker can group
  // them apart from the category's own.
  common: z.boolean(),
  // Which schema slugs define this key. One entry for a category that
  // resolved to a single slug; several for a root resolved as a class
  // union (`esr` is electrolytic + tantalum); none when the category
  // resolved to no slug and no class, i.e. the common keys only.
  slugs: z.array(z.string()),
});
export type CategorySpecKey = z.infer<typeof CategorySpecKeySchema>;

/**
 * `GET /api/categories/{id}/spec-schema` — what this category's parts
 * list CAN show and what it IS configured to show.
 *
 * `slug` is null for a category the spec schema does not recognise, which
 * is not an error. With `class` set it means the union of that class's
 * slugs — a bare "Capacitors" offers every dielectric's keys, because the
 * parts under it carry them; without one it means the common keys only.
 * `list_columns` / `list_sort` are resolved up the tree, and
 * `inherited_from` / `sort_inherited_from` name the ancestor each came
 * from (null when this category owns it, or when nobody has set one).
 */
export const CategorySpecSchemaSchema = z.object({
  slug: nullableString,
  class: nullableString,
  keys: z.array(CategorySpecKeySchema),
  list_columns: z.array(z.string()).nullable(),
  list_sort: CategoryListSortSchema.nullable(),
  inherited_from: uuid.nullable(),
  sort_inherited_from: uuid.nullable(),
});
export type CategorySpecSchema = z.infer<typeof CategorySpecSchemaSchema>;

// ---------------------------------------------------------------------
// API tokens (PATs) — the non-cookie credential for KiCad and agents.
// Backend: `app/domain/tokens/schemas.py`. `user_email` is only
// populated by the admin-only `?all=true` listing.
// ---------------------------------------------------------------------

export const ApiTokenSchema = z.object({
  id: uuid,
  label: z.string(),
  read_only: z.boolean(),
  created_at: z.string(),
  expires_at: nullableString,
  revoked_at: nullableString,
  last_used_at: nullableString,
  user_email: nullableString,
});
export type ApiToken = z.infer<typeof ApiTokenSchema>;

export const ApiTokensListSchema = z.array(ApiTokenSchema);

/** Mint response. `token` is the plaintext, shown once and never again —
 * the server stores only its HMAC and has no recovery path. */
export const ApiTokenCreatedSchema = ApiTokenSchema.extend({
  token: z.string(),
});
export type ApiTokenCreated = z.infer<typeof ApiTokenCreatedSchema>;

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

/**
 * One library row an import touched. `created: false` means the row
 * already held these exact bytes — the store is content-addressed, so a
 * re-import reuses instead of duplicating.
 */
export const EdaImportRowSchema = z.object({
  id: uuid,
  name: z.string(),
  created: z.boolean(),
  kind: nullableString.optional(),
});
export type EdaImportRow = z.infer<typeof EdaImportRowSchema>;

/** A member the importer deliberately did not take, and why. */
export const EdaImportSkipSchema = z.object({
  filename: z.string(),
  reason: z.string(),
});
export type EdaImportSkip = z.infer<typeof EdaImportSkipSchema>;

/**
 * The result of a part-bound import — a vendor zip or an LCSC fetch.
 * Backend: `app/domain/eda/schemas.py::PartEdaImportOut`.
 */
export const PartEdaImportSchema = z.object({
  /** snapeda | samacsys | ultralibrarian | easyeda. */
  vendor: z.string(),
  symbol: EdaImportRowSchema.nullable(),
  footprint: EdaImportRowSchema.nullable(),
  datafiles: z.array(EdaImportRowSchema),
  part_eda_updated: z.boolean(),
  skipped: z.array(EdaImportSkipSchema),
});
export type PartEdaImport = z.infer<typeof PartEdaImportSchema>;

/**
 * Everything the KiCad setup page needs, minus the token.
 * Backend: `app/api/routes/eda.py::kicad_setup`.
 *
 * `example` is a `.kicad_httplib` document with a placeholder where the
 * secret goes — a token's plaintext exists once, in the response that
 * minted it, so the server cannot hand out a ready-to-use file. Note
 * `meta.version` is a NUMBER; KiCad refuses to load the file if it is
 * quoted, which is why it is typed here rather than passed through.
 */
export const KicadSetupSchema = z.object({
  root_url: z.string(),
  categories_ttl: z.number(),
  parts_ttl: z.number(),
  pcm_repository_url_template: z.string(),
  pcm_package_identifier: z.string(),
  pcm_spice_path_variable: z.string(),
  pcm_spice_path_value: z.string(),
  read_only_note: z.string(),
  /** Null when the server was started with `MCP_ENABLED=false`. */
  mcp_url: nullableString,
  mcp_note: z.string(),
  example: z.object({
    meta: z.object({ version: z.number() }),
    name: z.string(),
    source: z.object({
      type: z.string(),
      api_version: z.string(),
      root_url: z.string(),
      token: z.string(),
      timeout_parts_seconds: z.number(),
      timeout_categories_seconds: z.number(),
    }),
  }),
});
export type KicadSetup = z.infer<typeof KicadSetupSchema>;

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
  // Added by alembic 0081; NULL on every row until the spec schema is
  // wired into import/refresh. `.optional()` accepts a payload that omits
  // them (an older server, a fixture); `.default(null)` means the inferred
  // TYPE still has both keys, so a `CustomFieldRow` literal must supply
  // them. `value_num` is the SI base-unit number behind `value`, sent as a
  // fixed-point string to stay exact — sort and filter on it server-side,
  // don't compare it in JS.
  provider: nullableString.optional().default(null),
  value_num: nullableString.optional().default(null),
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
  // Per-BOM-line waste rate (Track B1). 0 <= pct < 100; inflates the
  // build's required + consumed quantity.
  attrition_pct: z.number(),
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
