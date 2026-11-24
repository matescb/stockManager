import { describe, expect, it } from "vitest";
import type { Attachment } from "@/types";
import { resolveDatasheetLink, withDownloadName } from "../datasheetLink";

const UPSTREAM = "https://www.mouser.com/datasheet/2/lm358.pdf";

function attachment(overrides: Partial<Attachment> = {}): Attachment {
  return {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    object_type: "part",
    object_id: "p1",
    file_name: "LM358DR.pdf",
    file_type: "datasheet",
    mime_type: "application/pdf",
    size_bytes: 2048,
    created_at: "2026-11-20T10:00:00Z",
    ...overrides,
  };
}

describe("resolveDatasheetLink", () => {
  it("prefers a stored datasheet attachment and keeps the upstream URL as source", () => {
    const link = resolveDatasheetLink([attachment()], UPSTREAM, "LM358DR");
    expect(link).toEqual({
      kind: "local",
      href: "/api/attachments/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/download",
      fileName: "LM358DR.pdf",
      sizeBytes: 2048,
      sourceUrl: UPSTREAM,
    });
  });

  it("ignores non-datasheet attachments", () => {
    const link = resolveDatasheetLink(
      [attachment({ file_type: "image", mime_type: "image/png" })],
      UPSTREAM,
      "LM358DR",
    );
    expect(link).toEqual({ kind: "external", href: UPSTREAM });
  });

  it("picks the first (newest) datasheet attachment", () => {
    const link = resolveDatasheetLink(
      [attachment({ id: "new" }), attachment({ id: "old" })],
      null,
      null,
    );
    expect(link?.kind === "local" && link.href).toBe("/api/attachments/new/download");
  });

  it("falls back to the upstream URL when nothing is stored", () => {
    expect(resolveDatasheetLink([], UPSTREAM, "x")).toEqual({ kind: "external", href: UPSTREAM });
    expect(resolveDatasheetLink(undefined, UPSTREAM, "x")).toEqual({ kind: "external", href: UPSTREAM });
  });

  it("treats a legacy local asset URL as local and adds a download name", () => {
    const link = resolveDatasheetLink([], "/api/parts/assets/ws-1/abc.pdf", "LM358 DR");
    expect(link).toEqual({
      kind: "local",
      href: "/api/parts/assets/ws-1/abc.pdf?name=LM358_DR",
      fileName: null,
      sizeBytes: null,
      sourceUrl: null,
    });
  });

  it("drops a non-http upstream URL", () => {
    expect(resolveDatasheetLink([], "javascript:alert(1)", "x")).toBeNull();
    const link = resolveDatasheetLink([attachment()], "javascript:alert(1)", "x");
    expect(link?.kind === "local" && link.sourceUrl).toBeNull();
  });

  it("returns null with no attachment and no URL", () => {
    expect(resolveDatasheetLink([], null, "x")).toBeNull();
  });
});

describe("withDownloadName", () => {
  it("only decorates our own asset paths", () => {
    expect(withDownloadName(UPSTREAM, "x")).toBe(UPSTREAM);
    expect(withDownloadName("/api/parts/assets/w/a.pdf?v=1", "a b")).toBe(
      "/api/parts/assets/w/a.pdf?v=1&name=a_b",
    );
  });
});
