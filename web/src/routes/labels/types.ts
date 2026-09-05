/**
 * The label-template contract, client side.
 *
 * Every shape here mirrors a server-side one and the citation is deliberate —
 * this file is the place a backend change has to be reflected:
 *
 *   entity types   `domain/codes/models.py::CODE_ENTITY_TYPES`
 *   element kinds  `domain/printing/models.py::ELEMENT_KINDS`
 *   element fields `domain/printing/schemas.py::ElementIn` (+ the per-kind
 *                  knobs `domain/printing/label_render.py` reads)
 *   template       `domain/printing/schemas.py::TemplateOut`
 *   bindings       `domain/printing/template_service.py::_base_context`
 *                  and `_entity_fields`
 *
 * `elements` comes back from the API as raw JSONB (`list[dict[str, Any]]`),
 * so it is parsed through zod at the boundary: a template written by an older
 * build, or by the server's own `POST /defaults` seeder, must load rather
 * than blow up the designer. Unknown/undrawable kinds are dropped on parse
 * for the same reason the renderer skips them.
 */
import { z } from "zod";

// ---------------------------------------------------------------------
// Enumerations
// ---------------------------------------------------------------------

/** The five codeable entity types. Mirrors `CODE_ENTITY_TYPES`. */
export const LABEL_ENTITY_TYPES = [
  "part",
  "lot",
  "storage_location",
  "order",
  "build",
] as const;
export type LabelEntityType = (typeof LABEL_ENTITY_TYPES)[number];

/** Human labels for the entity tabs. */
export const ENTITY_TYPE_LABELS: Record<LabelEntityType, string> = {
  part: "Part",
  lot: "Lot",
  storage_location: "Storage location",
  order: "Order",
  build: "Build",
};

/** Mirrors `printing/models.py::ELEMENT_KINDS`. No `image` — the renderer
 *  has no image element, so the designer must not offer one. */
export const ELEMENT_KINDS = ["qr", "text", "barcode1d", "handwriting"] as const;
export type ElementKind = (typeof ELEMENT_KINDS)[number];

export const ELEMENT_KIND_LABELS: Record<ElementKind, string> = {
  qr: "QR code",
  text: "Text",
  barcode1d: "Barcode",
  handwriting: "Line",
};

/** QR error correction. `label_render._QR_EC_LEVELS`. */
export const QR_EC_LEVELS = ["L", "M", "Q", "H"] as const;
export type QrEcLevel = (typeof QR_EC_LEVELS)[number];

/** T = thermal transfer (ribbon), D = direct thermal. */
export const PRINT_METHODS = ["T", "D"] as const;
export type PrintMethod = (typeof PRINT_METHODS)[number];

/**
 * cab device fonts, by the numeric id the JScript `T` command takes.
 * `label_render` defaults to 3 and the built-in templates use 3 and 5.
 * A named (downloaded TrueType) font is also accepted by the renderer, but
 * only fonts actually installed on the device work — so the picker offers the
 * three built-ins and a free-text escape hatch.
 */
export const DEVICE_FONTS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 3, label: "Swiss 721" },
  { value: 5, label: "Swiss 721 Bold" },
  { value: 596, label: "Monospace 821" },
];

/** 1D symbologies the cab firmware accepts for the `B` command. */
export const BARCODE_TYPES = [
  "CODE128",
  "CODE39",
  "EAN13",
  "EAN8",
  "ITF",
  "UPCA",
] as const;

// ---------------------------------------------------------------------
// Bindings
// ---------------------------------------------------------------------

/**
 * Bindings every entity type resolves — `template_service._base_context`.
 * `entity_type` and `workspace` are there too but are rarely wanted on a
 * label, so they sit at the end of the list rather than being hidden.
 */
export const COMMON_BINDINGS = [
  "code",
  "url",
  "name",
  "description",
  "workspace",
  "entity_type",
] as const;

/**
 * The extra bindings each type fills, from
 * `template_service._entity_fields`. A token missing here still resolves
 * (to "") — this list drives the picker, not the renderer.
 */
export const ENTITY_BINDINGS: Record<LabelEntityType, readonly string[]> = {
  part: ["mpn", "manufacturer"],
  lot: ["serial", "part_name", "mpn", "manufacturer"],
  storage_location: [],
  order: ["supplier", "status"],
  build: ["project_name", "quantity", "status"],
};

/** Every binding offered for an entity type, common ones first. */
export function bindingsFor(entity: LabelEntityType): string[] {
  return [...COMMON_BINDINGS, ...ENTITY_BINDINGS[entity]];
}

// ---------------------------------------------------------------------
// Elements
// ---------------------------------------------------------------------

/**
 * A designer-local element id. NOT persisted: it is stripped before the
 * template is sent (see `data.ts::toElementPayload`) so the stored JSONB
 * stays byte-comparable with what the server's own seeder writes.
 */
const elementId = () =>
  `el_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;

const baseElement = {
  id: z.string().default(elementId),
  x_mm: z.coerce.number().catch(0),
  y_mm: z.coerce.number().catch(0),
  rotation: z.coerce.number().catch(0),
};

export const QrElementSchema = z.object({
  ...baseElement,
  kind: z.literal("qr"),
  dotsize_mm: z.coerce.number().catch(0.5),
  ec: z.enum(QR_EC_LEVELS).catch("M"),
  text: z.string().nullish(),
  binding: z.string().nullish(),
});

export const TextElementSchema = z.object({
  ...baseElement,
  kind: z.literal("text"),
  text: z.string().nullish(),
  binding: z.string().nullish(),
  font: z.union([z.number(), z.string()]).catch(3),
  size_pt: z.coerce.number().catch(8),
});

export const Barcode1dElementSchema = z.object({
  ...baseElement,
  kind: z.literal("barcode1d"),
  bc_type: z.string().catch("CODE128"),
  height_mm: z.coerce.number().catch(8),
  ne_mm: z.coerce.number().catch(0.4),
  text: z.string().nullish(),
  binding: z.string().nullish(),
});

/**
 * A ruled line for the operator to write on. Rendered by the JScript `G`
 * element, where `w_mm` is the line LENGTH and `h_mm` its THICKNESS
 * (`label_render._handwriting_line`).
 */
export const HandwritingElementSchema = z.object({
  ...baseElement,
  kind: z.literal("handwriting"),
  w_mm: z.coerce.number().catch(20),
  h_mm: z.coerce.number().catch(0.3),
});

export const ElementSchema = z.discriminatedUnion("kind", [
  QrElementSchema,
  TextElementSchema,
  Barcode1dElementSchema,
  HandwritingElementSchema,
]);

export type LabelElement = z.infer<typeof ElementSchema>;
export type QrElement = z.infer<typeof QrElementSchema>;
export type TextElement = z.infer<typeof TextElementSchema>;
export type Barcode1dElement = z.infer<typeof Barcode1dElementSchema>;
export type HandwritingElement = z.infer<typeof HandwritingElementSchema>;

/**
 * Parse a stored `elements` array, dropping anything undrawable.
 *
 * Dropping rather than failing is the same call the renderer makes: a
 * template carrying a kind this build cannot draw should still open in the
 * designer (minus that element) instead of locking the operator out of every
 * other element on the label.
 */
export const ElementListSchema = z
  .array(z.unknown())
  .catch([])
  .transform((raw) =>
    raw.flatMap((item) => {
      const parsed = ElementSchema.safeParse(item);
      return parsed.success ? [parsed.data] : [];
    }),
  );

// ---------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------

export const TemplateSchema = z.object({
  id: z.string(),
  name: z.string(),
  entity_type: z.enum(LABEL_ENTITY_TYPES),
  width_mm: z.coerce.number(),
  height_mm: z.coerce.number(),
  gap_mm: z.coerce.number().catch(3),
  heat: z.coerce.number().catch(100),
  speed: z.coerce.number().catch(0),
  method: z.enum(PRINT_METHODS).catch("T"),
  dpi: z.coerce.number().catch(300),
  is_default: z.boolean().catch(false),
  elements: ElementListSchema,
});

export type LabelTemplate = z.infer<typeof TemplateSchema>;

export const TemplateListSchema = z.array(TemplateSchema);

/** `GET /api/label-templates/{id}/jscript` — `RenderOut`. */
export const RenderSchema = z.object({ jscript: z.string() });

/** `POST /api/label-templates/{id}/test-print` — `TestPrintOut`. */
export const TestPrintSchema = z.object({
  print_job_id: z.string(),
  status: z.string(),
  code: z.string().nullish(),
});
export type TestPrintResult = z.infer<typeof TestPrintSchema>;

/**
 * The draft a template is edited as. `id` is `null` until the first save —
 * the designer has to represent an unsaved template, and the server assigns
 * UUIDs.
 */
export type TemplateDraft = Omit<LabelTemplate, "id"> & { id: string | null };

/** Geometry bounds mirrored from `printing/schemas.py` (`_MM_MAX` et al). */
export const LIMITS = {
  MM_MAX: 500,
  GAP_MAX: 50,
  HEAT_MAX: 200,
  SPEED_MAX: 400,
  DPI_MIN: 100,
  DPI_MAX: 1200,
  MAX_ELEMENTS: 100,
  MAX_TEXT: 2000,
  MAX_COPIES: 20,
} as const;
