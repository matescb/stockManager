/**
 * The wire shapes behind per-category spec columns.
 *
 * These mirror `backend/app/domain/parts/services/spec_columns.py::
 * serialize_schema` and the `specs` block `serialize_part` adds. Drift
 * between the two is a parse error in the running app rather than a blank
 * column, which is only true if the schemas actually refuse the wrong
 * shape — hence the negative cases.
 */
import { describe, expect, it } from "vitest";
import {
  CategoryListSortSchema,
  CategorySpecSchemaSchema,
  PartCategorySchema,
  PartSchema,
} from "../schemas";

const categoryId = "44444444-4444-4444-8444-444444444444";
const parentId = "55555555-5555-4555-8555-555555555555";
const partId = "11111111-1111-4111-8111-111111111111";

const SCHEMA_PAYLOAD = {
  slug: "resistor",
  keys: [
    {
      key: "resistance",
      label: "Resistance",
      unit: "Ω",
      mandatory: true,
      numeric: true,
      common: false,
    },
    {
      key: "package",
      label: "Package",
      unit: null,
      mandatory: true,
      numeric: false,
      common: true,
    },
  ],
  list_columns: ["resistance", "package"],
  list_sort: { key: "resistance", dir: "asc" },
  inherited_from: parentId,
  sort_inherited_from: null,
};

const CATEGORY_PAYLOAD = {
  id: categoryId,
  name: "Resistors",
  description: null,
  sort_order: 0,
  refdes_prefix: "R",
  default_symbol_ref: null,
  default_footprint_ref: null,
  footprint_filters: null,
  value_template: null,
  kicad_fields: null,
  library_slug: "resistors",
  parent_id: null,
  archived_at: null,
};

describe("CategorySpecSchemaSchema", () => {
  it("accepts the spec-schema endpoint's payload", () => {
    const parsed = CategorySpecSchemaSchema.parse(SCHEMA_PAYLOAD);
    expect(parsed.slug).toBe("resistor");
    expect(parsed.keys.map(k => k.key)).toEqual(["resistance", "package"]);
    expect(parsed.list_sort).toEqual({ key: "resistance", dir: "asc" });
    expect(parsed.inherited_from).toBe(parentId);
  });

  it("accepts a category the spec schema does not recognise", () => {
    // `slug: null` is not an error — it means the common keys only, and a
    // frontend that treated it as one would break every uncategorised or
    // oddly-named branch of the tree.
    const parsed = CategorySpecSchemaSchema.parse({
      ...SCHEMA_PAYLOAD,
      slug: null,
      list_columns: null,
      list_sort: null,
      inherited_from: null,
    });
    expect(parsed.slug).toBeNull();
    expect(parsed.list_columns).toBeNull();
  });

  it("keeps the empty column list distinct from null", () => {
    // `[]` is "no spec columns here" and stops the inheritance walk; null
    // is "inherit". Collapsing them would make an explicit opt-out
    // indistinguishable from never having chosen.
    expect(
      CategorySpecSchemaSchema.parse({ ...SCHEMA_PAYLOAD, list_columns: [] })
        .list_columns,
    ).toEqual([]);
  });

  it("rejects a key row missing its display fields", () => {
    expect(() =>
      CategorySpecSchemaSchema.parse({
        ...SCHEMA_PAYLOAD,
        keys: [{ key: "resistance", label: "Resistance" }],
      }),
    ).toThrow();
  });
});

describe("CategoryListSortSchema", () => {
  it("accepts both directions and refuses anything else", () => {
    expect(CategoryListSortSchema.parse({ key: "power", dir: "desc" }).dir).toBe(
      "desc",
    );
    expect(() =>
      CategoryListSortSchema.parse({ key: "power", dir: "sideways" }),
    ).toThrow();
  });
});

describe("PartCategorySchema", () => {
  it("defaults the list settings when an older payload omits them", () => {
    // `.optional().default(null)` rather than a bare `.nullable()`: a
    // response cached before this feature shipped must still parse, while
    // the inferred TYPE still carries both keys.
    const parsed = PartCategorySchema.parse(CATEGORY_PAYLOAD);
    expect(parsed.list_columns).toBeNull();
    expect(parsed.list_sort).toBeNull();
  });

  it("round-trips a stored column choice and sort", () => {
    const parsed = PartCategorySchema.parse({
      ...CATEGORY_PAYLOAD,
      list_columns: ["resistance", "tolerance"],
      list_sort: { key: "tolerance", dir: "desc" },
    });
    expect(parsed.list_columns).toEqual(["resistance", "tolerance"]);
    expect(parsed.list_sort).toEqual({ key: "tolerance", dir: "desc" });
  });
});

describe("PartSchema.specs", () => {
  const row = {
    id: partId,
    part_type: "local",
    name: "Resistor 10k",
    manufacturer: null,
    mpn: null,
    internal_part_number: null,
    description: null,
    footprint: null,
    notes_markdown: null,
    low_stock_report_quantity: null,
    attrition_percentage: 0,
    attrition_min_quantity: 0,
    default_storage_location_id: null,
    default_storage_mandatory: false,
    serialized: false,
    category_id: categoryId,
    linked_provider: null,
    linked_external_id: null,
    last_refresh_at: null,
    description_locally_edited: false,
    archived_at: null,
    on_hand: 0,
    reserved: 0,
    available: 0,
    image_url: null,
  };

  it("is absent when the request asked for no spec columns", () => {
    // Absent, not `{}`: the endpoint's default shape must be unchanged for
    // the dozen lookup-style consumers of `GET /parts`.
    expect(PartSchema.parse(row).specs).toBeUndefined();
  });

  it("carries a null value for a requested key the part has no row for", () => {
    const parsed = PartSchema.parse({
      ...row,
      specs: {
        resistance: { value: "10 kΩ", value_num: "10000" },
        power: { value: null, value_num: null },
      },
    });
    expect(parsed.specs?.resistance).toEqual({
      value: "10 kΩ",
      // A fixed-point STRING: `NUMERIC(36,18)` is exact and a JS double is
      // not, so this is never compared in JS.
      value_num: "10000",
    });
    expect(parsed.specs?.power).toEqual({ value: null, value_num: null });
  });

  it("rejects a numeric `value_num`", () => {
    expect(() =>
      PartSchema.parse({
        ...row,
        specs: { resistance: { value: "10 kΩ", value_num: 10000 } },
      }),
    ).toThrow();
  });
});
