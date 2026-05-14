/**
 * Pinning tests for DataTable's CSV export + selection-prune helpers.
 *
 * The component-level concerns (effect prunes selected on rows change,
 * effect resets on tableId change) are exercised through the pure
 * helpers extracted from the component. The repo doesn't carry a DOM
 * testing harness (no `@testing-library/react`, no jsdom env), and
 * adding one is out of scope per the constraints — so we keep the
 * tests pure and deterministic.
 */
import { describe, expect, it } from "vitest";
import { buildCsv, dataTableStorageKey, escapeCsvCell, pruneSelection } from "./DataTable";

describe("escapeCsvCell — formula injection mitigation", () => {
  it("prefixes a leading '=' with a quote", () => {
    expect(escapeCsvCell("=SUM(A1:A2)")).toBe(`"'=SUM(A1:A2)"`);
  });

  it("prefixes leading '+' / '-' / '@' / TAB / CR", () => {
    expect(escapeCsvCell("+1")).toBe(`"'+1"`);
    expect(escapeCsvCell("-cmd")).toBe(`"'-cmd"`);
    expect(escapeCsvCell("@import")).toBe(`"'@import"`);
    expect(escapeCsvCell("\tnah")).toBe(`"'\tnah"`);
  });

  it("does not neutralise numbers — only string cells get the prefix", () => {
    // Negative numbers are legitimate, not formulas — so a *numeric*
    // -5 stays "-5", whereas a *string* "-5" gets the leading quote.
    expect(escapeCsvCell(-5)).toBe(`"-5"`);
    expect(escapeCsvCell("-5")).toBe(`"'-5"`);
  });

  it("doubles embedded quotes per RFC 4180", () => {
    expect(escapeCsvCell('he said "hi"')).toBe(`"he said ""hi"""`);
  });

  it("renders null / undefined as empty cells", () => {
    expect(escapeCsvCell(null)).toBe(`""`);
    expect(escapeCsvCell(undefined)).toBe(`""`);
  });

  it("handles plain text without modification", () => {
    expect(escapeCsvCell("hello world")).toBe(`"hello world"`);
  });

  it("preserves embedded CR/LF inside the cell (round-trip safety)", () => {
    // The cell itself can contain \r\n; the row separator is also
    // \r\n. Quotes are RFC-4180-compliant so the embedded newlines
    // round-trip rather than splitting the row.
    expect(escapeCsvCell("line1\r\nline2")).toBe(`"line1\r\nline2"`);
  });
});

describe("buildCsv", () => {
  it("starts with a UTF-8 BOM so Excel detects encoding", () => {
    const csv = buildCsv(["a"], [["x"]]);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
  });

  it("uses CRLF as the line terminator", () => {
    const csv = buildCsv(["a", "b"], [["1", "2"], ["3", "4"]]);
    // strip BOM
    const body = csv.slice(1);
    expect(body).toBe(`"a","b"\r\n"1","2"\r\n"3","4"`);
  });

  it("neutralises a leading-`=` cell anywhere in the body", () => {
    const csv = buildCsv(["formula"], [["=SUM(1,1)"]]);
    expect(csv).toContain(`"'=SUM(1,1)"`);
  });

  it("round-trips embedded CRLF inside a cell", () => {
    const csv = buildCsv(["multiline"], [["a\r\nb"]]);
    // The header row + one body row, with the embedded \r\n preserved
    // inside the quoted cell rather than splitting into two records.
    expect(csv.slice(1)).toBe(`"multiline"\r\n"a\r\nb"`);
  });
});

describe("pruneSelection", () => {
  it("removes ids that are no longer in the row list", () => {
    const sel = new Set(["a", "b", "c"]);
    const next = pruneSelection(sel, ["a", "c", "d"]);
    expect([...next].sort()).toEqual(["a", "c"]);
  });

  it("returns an empty set when no rows survive", () => {
    expect(pruneSelection(new Set(["x"]), [])).toEqual(new Set());
  });

  it("preserves order-independence between input set and rows", () => {
    const sel = new Set(["a", "b"]);
    const next = pruneSelection(sel, ["b", "a"]);
    expect(next.has("a")).toBe(true);
    expect(next.has("b")).toBe(true);
  });
});

describe("DataTable persistence", () => {
  it("test_prefs_per_workspace", () => {
    expect(dataTableStorageKey("parts", "ws-a")).toBe("ws:ws-a:dt:parts");
    expect(dataTableStorageKey("parts", "ws-b")).toBe("ws:ws-b:dt:parts");
    expect(dataTableStorageKey("parts", undefined)).toBe("ws:none:dt:parts");
    expect(dataTableStorageKey(undefined, "ws-a")).toBeUndefined();
  });
});
