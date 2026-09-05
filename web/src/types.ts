/**
 * Public type re-exports.
 *
 * Resource types are inferred from zod schemas in `lib/schemas.ts`,
 * so this module is the single TypeScript-facing facade. Keep new
 * resource types in `schemas.ts` and re-export here.
 *
 * Pre-PR-#15 these were standalone TypeScript declarations. Migrating
 * them to `z.infer` outputs gives us:
 *  - One source of truth for the response shape (the zod schema).
 *  - Runtime validation via `api.parsed.*` for callers that opt in.
 *  - Compile-time identity for callers still using `api.get<T>` —
 *    no breakage during incremental migration.
 */

// Resources currently covered by zod schemas.
export type {
  Part,
  ProviderLink,
  PartCategory,
  ApiToken,
  ApiTokenCreated,
  EdaSymbol,
  EdaFootprint,
  EdaDatafile,
  EdaFootprintModel,
  PartEda,
  PartEdaWrite,
  PartEdaImport,
  EdaImportRow,
  EdaImportSkip,
  KicadSetup,
  StorageLocation,
  SpecSource,
  CustomFieldRow,
  Lot,
  StockEntry,
  Project,
  Order,
  OrderEntry,
  Build,
  ProjectEntry,
} from "./lib/schemas";

// Types not yet covered by zod schemas (kept inline for now).
// Migrate to `lib/schemas.ts` when their endpoints are touched.

export type BuildShortageRow = {
  project_entry_id: string;
  part_id: string;
  part_name: string;
  // Per-BOM-line waste rate that inflated `required` (Track B1).
  attrition_pct: number;
  required: number;
  available: number;
  substitute_ids: string[];
  substitute_available: number;
  short_by: number;
};

// Multi-stage builds (Track B2). A build with no stages is a single-pass
// build and these are simply absent.

export type BuildStageShortageRow = BuildShortageRow & {
  /** Percentage of the BOM line's whole-build requirement this stage takes. */
  portion_pct: number;
};

export type BuildStageLine = {
  id: string;
  project_entry_id: string;
  portion_pct: number;
};

export type BuildStage = {
  id: string;
  build_id: string;
  name: string;
  sequence: number;
  status: "planned" | "in_progress" | "complete";
  started_at: string | null;
  completed_at: string | null;
  comments: string | null;
  lines: BuildStageLine[];
  /** `required` here is this stage's slice, not the whole-build number. */
  shortage: BuildStageShortageRow[];
  created_at: string;
  updated_at: string;
};

export type PartsProviderName = "none" | "mouser" | "digikey";

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
