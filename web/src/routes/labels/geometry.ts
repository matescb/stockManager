/**
 * Pure geometry, snap and binding-resolution maths for the label designer.
 * No React, no DOM, no network — everything here is unit-tested in
 * `__tests__/geometry.test.ts`.
 *
 * Ported from the sibling skladVA project
 * (/mnt/data/WORK/sklad, `frontend/src/routes/labels/geometry.ts`), with its
 * single-tenant namespace helpers replaced by this codebase's one code
 * namespace and its "a QR is about 25 modules" guess replaced by a real
 * version lookup (see `qrModuleCount`).
 *
 * Coordinate model — MILLIMETRES
 * -----------------------------
 * The backend stores and renders every coordinate in mm: `label_templates`
 * has `width_mm` / `height_mm` columns and each element carries `x_mm` /
 * `y_mm`, which `domain/printing/label_render.py` feeds straight into the
 * cab JScript `T` / `B` / `G` commands (the printer is put in mm mode by the
 * job header). So the designer authors in mm too — there is no pixel model to
 * convert back, and no rounding trip through device pixels.
 *
 * Screen zoom is a separate, dpi-INDEPENDENT `pxPerMm` factor. Printer dpi
 * only affects the raster the printer burns, not the physical mm layout the
 * operator is designing, so a 300-dpi and a 600-dpi head produce the same
 * canvas.
 */
import type { LabelElement, LabelEntityType, QrEcLevel } from "./types";

/** Screen zoom: device-independent px per mm at 100% zoom. */
export const PX_PER_MM = 4;

/** Zoom stops offered by the editor, in px per mm. */
export const ZOOM_STEPS = [2, 3, 4, 6, 8, 12] as const;

/** Default snap grid, in mm. */
export const DEFAULT_GRID_MM = 1;

/** 1 pt = 1/72 inch = 0.352778 mm (matches `label_render._PT_TO_MM`). */
export const PT_TO_MM = 0.352778;

// ---------------------------------------------------------------------
// mm <-> px
// ---------------------------------------------------------------------

export function mmToPx(mm: number, pxPerMm: number = PX_PER_MM): number {
  return mm * pxPerMm;
}

export function pxToMm(px: number, pxPerMm: number = PX_PER_MM): number {
  return px / pxPerMm;
}

// ---------------------------------------------------------------------
// Snapping + clamping
// ---------------------------------------------------------------------

/**
 * Snap a single mm coordinate to the nearest grid line. A non-positive
 * `gridMm` means "no grid": we still round to 0.01 mm so a pointer drag does
 * not persist float noise like `12.700000000000001` into the JSONB blob.
 */
export function snapMm(value: number, gridMm: number): number {
  if (!Number.isFinite(value)) return 0;
  if (!Number.isFinite(gridMm) || gridMm <= 0) {
    return Math.round(value * 100) / 100;
  }
  return Math.round(value / gridMm) * gridMm;
}

/** Snap an `{x, y}` mm point. Returns a NEW point. */
export function snapPoint(
  point: Readonly<{ x: number; y: number }>,
  gridMm: number,
): { x: number; y: number } {
  return { x: snapMm(point.x, gridMm), y: snapMm(point.y, gridMm) };
}

/**
 * Clamp an element's top-left so its footprint stays inside the label.
 * Returns a NEW point (nothing here mutates its arguments).
 */
export function clampToLabel(
  point: Readonly<{ x: number; y: number }>,
  size: Readonly<{ w: number; h: number }>,
  label: Readonly<{ width_mm: number; height_mm: number }>,
): { x: number; y: number } {
  const maxX = Math.max(0, label.width_mm - size.w);
  const maxY = Math.max(0, label.height_mm - size.h);
  return {
    x: Math.min(Math.max(0, point.x), maxX),
    y: Math.min(Math.max(0, point.y), maxY),
  };
}

// ---------------------------------------------------------------------
// QR sizing
// ---------------------------------------------------------------------

/**
 * Byte-mode data capacity in BYTES for QR versions 1..40, per EC level.
 * Straight from ISO/IEC 18004 table 7. This is the only table we need: the
 * designer never encodes a symbol, it only needs to know how many modules
 * across the printer's symbol will come out so the preview box matches the
 * physical footprint.
 */
const QR_BYTE_CAPACITY: Readonly<Record<QrEcLevel, readonly number[]>> = {
  L: [
    17, 32, 53, 78, 106, 134, 154, 192, 230, 271, 321, 367, 425, 458, 520, 586,
    644, 718, 792, 858, 929, 1003, 1091, 1171, 1273, 1367, 1465, 1528, 1628,
    1732, 1840, 1952, 2068, 2188, 2303, 2431, 2563, 2699, 2809, 2953,
  ],
  M: [
    14, 26, 42, 62, 84, 106, 122, 152, 180, 213, 251, 287, 331, 362, 412, 450,
    504, 560, 624, 666, 711, 779, 857, 911, 997, 1059, 1125, 1190, 1264, 1370,
    1452, 1538, 1628, 1722, 1809, 1911, 1989, 2099, 2213, 2331,
  ],
  Q: [
    11, 20, 32, 46, 60, 74, 86, 108, 130, 151, 177, 203, 241, 258, 292, 322,
    364, 394, 442, 482, 509, 565, 611, 661, 715, 751, 805, 868, 908, 982, 1030,
    1112, 1168, 1228, 1283, 1351, 1423, 1499, 1579, 1663,
  ],
  H: [
    7, 14, 24, 34, 44, 58, 64, 84, 98, 119, 137, 155, 177, 194, 220, 250, 280,
    310, 338, 382, 403, 439, 461, 511, 535, 593, 625, 658, 698, 742, 790, 842,
    898, 958, 983, 1051, 1093, 1139, 1219, 1273,
  ],
};

/**
 * The smallest QR version (1..40) that holds `byteLength` bytes at `ec`, or
 * 40 when the payload overflows even version 40 — the preview degrades to the
 * largest symbol rather than throwing, because a too-long payload is the
 * printer's error to report, not the designer's to crash on.
 */
export function qrVersionFor(byteLength: number, ec: QrEcLevel): number {
  const table = QR_BYTE_CAPACITY[ec] ?? QR_BYTE_CAPACITY.M;
  const needed = Math.max(0, Math.floor(byteLength));
  const index = table.findIndex((capacity) => capacity >= needed);
  return index === -1 ? 40 : index + 1;
}

/** Modules along one edge of a QR symbol of the given version. */
export function qrModulesForVersion(version: number): number {
  const clamped = Math.min(40, Math.max(1, Math.round(version)));
  return 4 * clamped + 17;
}

/**
 * How many modules across the symbol for this payload will be. Used to size
 * the preview: the cab printer picks the version the same way, so
 * `modules * dotsize_mm` is the symbol's real printed side length.
 */
export function qrModuleCount(payload: string, ec: QrEcLevel): number {
  // UTF-8 byte length: the payload is a URL or a short code, but a name
  // binding can put non-ASCII in there and QR byte mode counts bytes.
  const bytes =
    typeof TextEncoder === "undefined"
      ? payload.length
      : new TextEncoder().encode(payload).length;
  return qrModulesForVersion(qrVersionFor(bytes, ec));
}

/** Printed side length in mm of a QR symbol for this payload. */
export function qrSideMm(
  payload: string,
  ec: QrEcLevel,
  dotsizeMm: number,
): number {
  const dot = Number.isFinite(dotsizeMm) && dotsizeMm > 0 ? dotsizeMm : 0.5;
  return qrModuleCount(payload, ec) * dot;
}

// ---------------------------------------------------------------------
// Footprints
// ---------------------------------------------------------------------

/**
 * Best-effort footprint (w, h in mm) of an element, for clamping and for the
 * selection outline. Approximate by construction for text and barcodes — the
 * printer rasterises the real glyphs and bars — but exact for a QR, whose
 * module count we can compute (`qrSideMm`).
 *
 * `resolvedText` is the binding-resolved display string when the caller has
 * one; without it we fall back to the literal/binding token so an unresolved
 * element still gets a sane box.
 */
export function elementFootprint(
  el: LabelElement,
  resolvedText?: string,
): { w: number; h: number } {
  switch (el.kind) {
    case "qr": {
      const payload = resolvedText ?? el.binding ?? el.text ?? "";
      const side = Math.max(4, qrSideMm(payload, el.ec, el.dotsize_mm));
      return { w: side, h: side };
    }
    case "text": {
      const text = resolvedText ?? el.text ?? el.binding ?? "";
      // Cap height is ~0.7 em; add ascender/descender headroom.
      const h = Math.max(1.5, el.size_pt * PT_TO_MM * 1.3);
      // ~0.6 em average advance width for a proportional face.
      const w = Math.max(2, text.length * el.size_pt * PT_TO_MM * 0.6);
      return { w, h };
    }
    case "barcode1d": {
      const payload = resolvedText ?? el.binding ?? el.text ?? "";
      // CODE128 is ~11 modules per encoded character plus quiet zones; this
      // is a preview estimate, not a spec-accurate width.
      const w = Math.max(6, (payload.length * 11 + 35) * el.ne_mm);
      return { w, h: Math.max(1, el.height_mm) };
    }
    case "handwriting":
      return { w: Math.max(1, el.w_mm), h: Math.max(0.2, el.h_mm) };
  }
}

// ---------------------------------------------------------------------
// Binding resolution
// ---------------------------------------------------------------------

/**
 * The sample values the canvas previews against. Deliberately the SAME
 * strings `template_service.sample_context` uses server-side, so what the
 * canvas shows and what `GET /{id}/jscript` renders agree.
 */
export interface SampleContext {
  readonly [token: string]: string;
}

/** The fixed, obviously-fake code the server's sample render uses. */
export const SAMPLE_CODE = "SAMPLE00";

/**
 * Build the sample context for an entity type. Mirrors
 * `backend/app/domain/printing/template_service.py::sample_context` — if that
 * changes, this must follow, or the preview quietly stops matching the print.
 */
export function sampleContext(
  entity: LabelEntityType,
  opts?: { origin?: string; workspace?: string },
): SampleContext {
  const origin = (opts?.origin ?? "").replace(/\/+$/, "");
  return {
    code: SAMPLE_CODE,
    url: `${origin}/c/${SAMPLE_CODE}`,
    entity_type: entity,
    workspace: opts?.workspace ?? "Workspace",
    name: `Sample ${entity.replace(/_/g, " ")}`,
    description: "Sample description",
    mpn: "SMPL-1234",
    manufacturer: "Sample Mfr",
    part_name: "Sample part",
    serial: "SN-0001",
    supplier: "Sample Supplier",
    status: "draft",
    project_name: "Sample project",
    quantity: "10",
  };
}

/** `{{token}}`, tolerating whitespace — same shape as `label_render._BINDING_RE`. */
const BINDING_RE = /\{\{\s*(\w+)\s*\}\}/g;

/** Strip the `{{ }}` wrapper off a binding token, if present. */
export function bindingToken(binding: string): string {
  return binding.replace(/^\{\{/, "").replace(/\}\}$/, "").trim();
}

/**
 * Resolve one binding token (with or without braces) against the context.
 * An unknown token resolves to "" — the server does the same, so a template
 * that outlives a binding rename previews exactly as it prints.
 */
export function resolveBinding(
  binding: string,
  ctx: Readonly<SampleContext>,
): string {
  return ctx[bindingToken(binding)] ?? "";
}

/** Resolve every `{{token}}` inside an arbitrary string. */
export function interpolate(
  template: string,
  ctx: Readonly<SampleContext>,
): string {
  return template.replace(BINDING_RE, (_match, token: string) => ctx[token] ?? "");
}

/**
 * The display value for a text/barcode element.
 *
 * Order matters and mirrors `label_render._resolve_text`: a literal `text`
 * WINS over a binding (and may itself embed `{{tokens}}`); only when there is
 * no literal is the `binding` token looked up.
 */
export function resolveTextValue(
  el: Readonly<{ text?: string | null; binding?: string | null }>,
  ctx: Readonly<SampleContext>,
): string {
  if (el.text != null && el.text !== "") return interpolate(el.text, ctx);
  if (el.binding) return resolveBinding(el.binding, ctx);
  return "";
}

/**
 * A QR element's payload. Mirrors `label_render._qr_payload`: a literal wins,
 * else the binding, else `{{url}}` — a QR with nothing set encodes the
 * scan-to-open link, because that is what a QR on a label is for.
 */
export function resolveQrPayload(
  el: Readonly<{ text?: string | null; binding?: string | null }>,
  ctx: Readonly<SampleContext>,
): string {
  if (el.text != null && el.text !== "") return interpolate(el.text, ctx);
  return resolveBinding(el.binding || "url", ctx);
}

/**
 * The JScript injection guard, mirrored client-side for the PREVIEW only.
 *
 * The server sanitises again on render (`label_render.sanitize`) and that is
 * the security boundary — this copy exists so the canvas shows what will
 * actually be printed rather than text the printer will never see.
 */
export function previewSanitize(value: string): string {
  // eslint-disable-next-line no-control-regex
  return value.replace(/[\x00-\x1f\x7f]/g, " ").replace(/;/g, " ").trim();
}
