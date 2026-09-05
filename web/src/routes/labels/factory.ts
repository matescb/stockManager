/**
 * Element and template factories, plus the serialisation that turns a
 * designer draft back into the request body the API expects.
 *
 * Ported from the sibling skladVA project
 * (/mnt/data/WORK/sklad, `frontend/src/routes/labels/factory.ts`). The
 * defaults here are deliberately the SAME numbers the renderer falls back to
 * (`backend/app/domain/printing/label_render.py`) and the seeder writes
 * (`default_templates.py`), so a hand-placed element and a seeded one print
 * identically.
 */
import {
  ELEMENT_KINDS,
  type ElementKind,
  type LabelElement,
  type LabelEntityType,
  type LabelTemplate,
  type TemplateDraft,
} from "./types";

/** A designer-local element id. Never persisted — see `toElementPayload`. */
export function newElementId(): string {
  return `el_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

/** A fresh element of `kind` at the given mm position. */
export function makeElement(
  kind: ElementKind,
  x_mm: number,
  y_mm: number,
): LabelElement {
  const base = { id: newElementId(), x_mm, y_mm, rotation: 0 };
  switch (kind) {
    case "qr":
      // No literal text: `label_render._qr_payload` defaults a bare QR to
      // `{{url}}`, the scan-to-open link, which is what a label QR is for.
      return { ...base, kind, dotsize_mm: 0.5, ec: "M", binding: "url" };
    case "text":
      return { ...base, kind, text: "Text", font: 3, size_pt: 8 };
    case "barcode1d":
      return {
        ...base,
        kind,
        bc_type: "CODE128",
        height_mm: 8,
        ne_mm: 0.4,
        binding: "code",
      };
    case "handwriting":
      // h_mm is the rule THICKNESS, not a box height (JScript `G ... L:len,width`).
      return { ...base, kind, w_mm: 20, h_mm: 0.3 };
  }
}

/** Type guard for a kind string arriving from outside (e.g. a drag payload). */
export function isElementKind(value: string): value is ElementKind {
  return (ELEMENT_KINDS as readonly string[]).includes(value);
}

/**
 * A blank, unsaved template. 50 x 30 mm / 3 mm gap / 300 dpi matches the
 * built-in stock in `default_templates.py`, so a hand-made template lands on
 * the same media as a seeded one unless the operator changes it.
 */
export function blankTemplate(entity: LabelEntityType): TemplateDraft {
  return {
    id: null,
    name: "",
    entity_type: entity,
    width_mm: 50,
    height_mm: 30,
    gap_mm: 3,
    heat: 100,
    speed: 0,
    method: "T",
    dpi: 300,
    is_default: false,
    elements: [],
  };
}

/**
 * A starter layout for a new template: the QR + human-readable code + name
 * arrangement every built-in default uses (`default_templates.py::_spec`),
 * so "New template" produces something printable rather than a blank sheet.
 */
export function starterTemplate(entity: LabelEntityType): TemplateDraft {
  return {
    ...blankTemplate(entity),
    elements: [
      {
        id: newElementId(),
        kind: "qr",
        x_mm: 2,
        y_mm: 2,
        rotation: 0,
        dotsize_mm: 0.5,
        ec: "M",
        binding: "url",
      },
      // The human-transcribable fallback for when the QR will not scan.
      {
        id: newElementId(),
        kind: "text",
        x_mm: 2,
        y_mm: 23,
        rotation: 0,
        binding: "code",
        font: 5,
        size_pt: 9,
      },
      {
        id: newElementId(),
        kind: "text",
        x_mm: 25,
        y_mm: 3,
        rotation: 0,
        binding: "name",
        font: 5,
        size_pt: 9,
      },
    ],
  };
}

/** An unsaved copy of an existing template, with fresh element ids. */
export function duplicateTemplate(tpl: LabelTemplate): TemplateDraft {
  return {
    ...tpl,
    id: null,
    name: `${tpl.name} (copy)`,
    // Never carry `is_default` into a copy: promoting it would silently
    // demote the incumbent the operator is still using.
    is_default: false,
    elements: tpl.elements.map((el) => ({ ...el, id: newElementId() })),
  };
}

/** A saved template opened for editing. */
export function toDraft(tpl: LabelTemplate): TemplateDraft {
  return { ...tpl, elements: tpl.elements.map((el) => ({ ...el })) };
}

// ---------------------------------------------------------------------
// Serialisation
// ---------------------------------------------------------------------

/** Round a mm value to 0.01 so pointer drags don't persist float noise. */
function mm(value: number): number {
  return Number.isFinite(value) ? Math.round(value * 100) / 100 : 0;
}

/**
 * One element as the API wants it.
 *
 * Two rules that are easy to get wrong and are the reason this is a function
 * rather than a spread:
 *
 *  1. `id` is dropped. `ElementIn` has `extra="allow"`, so a leaked designer
 *     id would be persisted into the JSONB and diverge from what the server's
 *     own seeder writes.
 *  2. An EMPTY `text` is dropped, not sent as `""`.
 *     `label_render._resolve_text` only falls through to `binding` when
 *     `text is None` — sending `text: ""` alongside a binding would render a
 *     blank field and quietly print an empty label.
 */
export function toElementPayload(el: LabelElement): Record<string, unknown> {
  const base: Record<string, unknown> = {
    kind: el.kind,
    x_mm: mm(el.x_mm),
    y_mm: mm(el.y_mm),
    rotation: ((Math.round(el.rotation) % 360) + 360) % 360,
  };

  const withBinding = (out: Record<string, unknown>) => {
    if (el.kind === "handwriting") return out;
    if (el.text != null && el.text !== "") out.text = el.text;
    else if (el.binding) out.binding = el.binding;
    return out;
  };

  switch (el.kind) {
    case "qr":
      return withBinding({ ...base, dotsize_mm: mm(el.dotsize_mm), ec: el.ec });
    case "text":
      return withBinding({
        ...base,
        font: el.font,
        size_pt: Math.round(el.size_pt),
      });
    case "barcode1d":
      return withBinding({
        ...base,
        bc_type: el.bc_type,
        height_mm: mm(el.height_mm),
        ne_mm: mm(el.ne_mm),
      });
    case "handwriting":
      return { ...base, w_mm: mm(el.w_mm), h_mm: mm(el.h_mm) };
  }
}

/** The body for `POST /api/label-templates`. */
export function toCreatePayload(draft: TemplateDraft): Record<string, unknown> {
  return {
    name: draft.name,
    entity_type: draft.entity_type,
    width_mm: mm(draft.width_mm),
    height_mm: mm(draft.height_mm),
    gap_mm: mm(draft.gap_mm),
    heat: Math.round(draft.heat),
    speed: Math.round(draft.speed),
    method: draft.method,
    dpi: Math.round(draft.dpi),
    is_default: draft.is_default,
    elements: draft.elements.map(toElementPayload),
  };
}

/**
 * The body for `PATCH /api/label-templates/{id}`.
 *
 * `entity_type` is deliberately absent: `TemplateUpdate` does not accept it
 * (retargeting would invalidate every binding on the label), and sending it
 * would be silently ignored rather than rejected.
 */
export function toUpdatePayload(draft: TemplateDraft): Record<string, unknown> {
  const { entity_type: _entityType, ...rest } = toCreatePayload(draft);
  return rest;
}
