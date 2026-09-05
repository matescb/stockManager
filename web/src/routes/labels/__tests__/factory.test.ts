/**
 * Unit tests for element/template construction and, more importantly, for
 * the serialisation that goes back over the wire.
 *
 * The three cases worth the file:
 *  - the designer-local element `id` never reaches the API (`ElementIn` has
 *    `extra="allow"`, so a leaked id would be persisted into the JSONB);
 *  - an empty `text` is OMITTED rather than sent as `""`, because
 *    `label_render._resolve_text` only falls through to `binding` when
 *    `text is None` — `""` would print a blank field;
 *  - `entity_type` is absent from the PATCH body (`TemplateUpdate` has no
 *    such field).
 */
import { describe, it, expect } from "vitest";
import {
  blankTemplate,
  duplicateTemplate,
  makeElement,
  starterTemplate,
  toCreatePayload,
  toElementPayload,
  toUpdatePayload,
} from "../factory";
import { ElementListSchema, TemplateSchema, type LabelTemplate } from "../types";

describe("makeElement", () => {
  it("gives every element a unique local id", () => {
    const a = makeElement("text", 0, 0);
    const b = makeElement("text", 0, 0);
    expect(a.id).not.toBe(b.id);
  });

  it("defaults a QR to the url binding with no literal text", () => {
    const el = makeElement("qr", 1, 2);
    expect(el).toMatchObject({ kind: "qr", binding: "url", ec: "M" });
    expect("text" in el && el.text).toBeFalsy();
  });

  it("treats a handwriting element's h_mm as a rule thickness, not a box", () => {
    const el = makeElement("handwriting", 0, 0);
    expect(el).toMatchObject({ kind: "handwriting", w_mm: 20, h_mm: 0.3 });
  });
});

describe("toElementPayload", () => {
  it("strips the designer-local id", () => {
    const payload = toElementPayload(makeElement("text", 1, 1));
    expect(payload).not.toHaveProperty("id");
  });

  it("omits an empty text so the binding is the one the renderer uses", () => {
    const el = { ...makeElement("text", 0, 0), text: "", binding: "code" };
    const payload = toElementPayload(el);
    expect(payload).not.toHaveProperty("text");
    expect(payload.binding).toBe("code");
  });

  it("sends the literal and drops the binding when both are set", () => {
    const el = { ...makeElement("text", 0, 0), text: "PART", binding: "code" };
    const payload = toElementPayload(el);
    expect(payload.text).toBe("PART");
    expect(payload).not.toHaveProperty("binding");
  });

  it("normalises rotation into 0..359", () => {
    const el = { ...makeElement("text", 0, 0), rotation: 450 };
    expect(toElementPayload(el).rotation).toBe(90);
    expect(toElementPayload({ ...el, rotation: -90 }).rotation).toBe(270);
  });

  it("rounds mm coordinates so pointer drags do not persist float noise", () => {
    const el = { ...makeElement("text", 12.700000000000001, 3.3333), rotation: 0 };
    const payload = toElementPayload(el);
    expect(payload.x_mm).toBe(12.7);
    expect(payload.y_mm).toBe(3.33);
  });

  it("carries no text/binding for a handwriting rule", () => {
    const payload = toElementPayload(makeElement("handwriting", 0, 0));
    expect(payload).not.toHaveProperty("text");
    expect(payload).not.toHaveProperty("binding");
    expect(payload).toMatchObject({ w_mm: 20, h_mm: 0.3 });
  });
});

describe("toCreatePayload / toUpdatePayload", () => {
  const draft = starterTemplate("part");

  it("includes entity_type on create", () => {
    expect(toCreatePayload({ ...draft, name: "X" }).entity_type).toBe("part");
  });

  it("omits entity_type on update — TemplateUpdate has no such field", () => {
    expect(toUpdatePayload({ ...draft, name: "X" })).not.toHaveProperty("entity_type");
  });

  it("serialises every element", () => {
    const payload = toCreatePayload(draft) as { elements: unknown[] };
    expect(payload.elements).toHaveLength(draft.elements.length);
  });
});

describe("duplicateTemplate", () => {
  const saved: LabelTemplate = TemplateSchema.parse({
    id: "11111111-1111-1111-1111-111111111111",
    name: "Part label",
    entity_type: "part",
    width_mm: 50,
    height_mm: 30,
    gap_mm: 3,
    heat: 100,
    speed: 0,
    method: "T",
    dpi: 300,
    is_default: true,
    elements: [{ kind: "qr", x_mm: 2, y_mm: 2, dotsize_mm: 0.5, ec: "M" }],
  });

  it("clears the id and the default flag", () => {
    const copy = duplicateTemplate(saved);
    expect(copy.id).toBeNull();
    expect(copy.is_default).toBe(false);
    expect(copy.name).toBe("Part label (copy)");
  });

  it("gives the copied elements fresh ids and does not mutate the original", () => {
    const copy = duplicateTemplate(saved);
    expect(copy.elements[0].id).not.toBe(saved.elements[0].id);
    expect(saved.elements[0].id).toBeTruthy();
  });
});

describe("blankTemplate", () => {
  it("matches the built-in 50x30 stock the server seeder uses", () => {
    expect(blankTemplate("lot")).toMatchObject({
      id: null,
      entity_type: "lot",
      width_mm: 50,
      height_mm: 30,
      gap_mm: 3,
      dpi: 300,
      method: "T",
      elements: [],
    });
  });
});

describe("ElementListSchema", () => {
  it("accepts a server-seeded element that carries no id", () => {
    const parsed = ElementListSchema.parse([
      { kind: "qr", x_mm: 2, y_mm: 2, dotsize_mm: 0.5, ec: "M" },
      { kind: "text", x_mm: 2, y_mm: 23, binding: "code", font: 5, size_pt: 9 },
    ]);
    expect(parsed).toHaveLength(2);
    expect(parsed.every((el) => typeof el.id === "string" && el.id.length > 0)).toBe(true);
  });

  it("drops an element kind this build cannot draw instead of failing the load", () => {
    const parsed = ElementListSchema.parse([
      { kind: "text", x_mm: 0, y_mm: 0, text: "keep" },
      { kind: "hologram", x_mm: 0, y_mm: 0 },
    ]);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].kind).toBe("text");
  });
});
