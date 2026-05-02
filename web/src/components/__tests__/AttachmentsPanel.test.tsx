/**
 * Unit tests for AttachmentsPanel's client-side file validation (#52).
 *
 * These tests cover `validateFile` (exported from AttachmentsPanel) and the
 * constants it relies on. Because the logic is pure — it only inspects the
 * File object fields and the constant maps — no jsdom or React render is
 * needed. The component-level wiring (onChange, onDrop both call validateFile
 * and call toast.error on failure without reaching api.upload) is verified
 * here through the helper directly; the consumer code paths are exercised by
 * reading the implementation.
 *
 * Covered:
 *  - Oversized file → error string (no upload should be triggered)
 *  - File with wrong extension for typed bucket → error string
 *  - File with correct extension → null (ok)
 *  - File with no extension but correct MIME → null (ok)
 *  - `other` type accepts any extension → null (ok)
 *  - `other` type still rejects oversized files → error string
 *  - humanSize helper edge cases
 *  - MAX_BYTES constant is 10 MiB (matches backend MAX_UPLOAD_BYTES)
 */
import { describe, it, expect } from "vitest";
import {
  validateFile,
  MAX_BYTES,
  ALLOWED_MIME_FOR_TYPE,
  humanSize,
} from "../AttachmentsPanel";

/**
 * Create a minimal File-like object that satisfies the `validateFile`
 * signature (which only reads `.name`, `.size`, and `.type`).
 *
 * The tests run in the default Node environment — `File` is not available
 * there, so we use a plain object cast to File.  This avoids a dependency
 * on jsdom (a whole new env) just to test a pure validation function.
 */
function makeFileWithSize(name: string, size: number, type = ""): File {
  return { name, size, type } as unknown as File;
}

// ---------------------------------------------------------------------------
// MAX_BYTES
// ---------------------------------------------------------------------------
describe("MAX_BYTES", () => {
  it("is 10 MiB (matches backend MAX_UPLOAD_BYTES)", () => {
    expect(MAX_BYTES).toBe(10 * 1024 * 1024);
  });
});

// ---------------------------------------------------------------------------
// humanSize
// ---------------------------------------------------------------------------
describe("humanSize", () => {
  it("shows bytes for small values", () => {
    expect(humanSize(512)).toBe("512 B");
  });

  it("shows KB for kilobyte range", () => {
    expect(humanSize(1024)).toBe("1.0 KB");
    expect(humanSize(2048)).toBe("2.0 KB");
  });

  it("shows MB for megabyte range", () => {
    expect(humanSize(1024 * 1024)).toBe("1.0 MB");
    expect(humanSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("shows GB for gigabyte range", () => {
    expect(humanSize(1024 * 1024 * 1024)).toBe("1.00 GB");
  });
});

// ---------------------------------------------------------------------------
// validateFile — size guard
// ---------------------------------------------------------------------------
describe("validateFile — size guard", () => {
  it("accepts a file exactly at MAX_BYTES", () => {
    const f = makeFileWithSize("ok.pdf", MAX_BYTES, "application/pdf");
    expect(validateFile(f, "datasheet")).toBeNull();
  });

  it("rejects a file one byte over MAX_BYTES", () => {
    const f = makeFileWithSize("big.pdf", MAX_BYTES + 1, "application/pdf");
    const result = validateFile(f, "datasheet");
    expect(result).not.toBeNull();
    expect(result).toContain("too large");
    expect(result).toContain(humanSize(MAX_BYTES));
  });

  it("includes the file's actual size in the rejection message", () => {
    const bigSize = MAX_BYTES + 1024 * 1024;
    const f = makeFileWithSize("huge.pdf", bigSize, "application/pdf");
    const result = validateFile(f, "datasheet");
    expect(result).toContain(humanSize(bigSize));
  });
});

// ---------------------------------------------------------------------------
// validateFile — datasheet
// ---------------------------------------------------------------------------
describe("validateFile — datasheet", () => {
  it("accepts a .pdf file", () => {
    const f = makeFileWithSize("spec.pdf", 1024, "application/pdf");
    expect(validateFile(f, "datasheet")).toBeNull();
  });

  it("accepts .pdf by extension even when MIME is empty (network-share scenario)", () => {
    const f = makeFileWithSize("spec.pdf", 1024, "");
    expect(validateFile(f, "datasheet")).toBeNull();
  });

  it("rejects a .docx file", () => {
    const f = makeFileWithSize("report.docx", 1024, "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    const result = validateFile(f, "datasheet");
    expect(result).not.toBeNull();
    expect(result).toContain(".pdf");
  });

  it("rejects a .exe file", () => {
    const f = makeFileWithSize("malware.exe", 1024, "application/octet-stream");
    const result = validateFile(f, "datasheet");
    expect(result).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// validateFile — image
// ---------------------------------------------------------------------------
describe("validateFile — image", () => {
  it("accepts a .jpg file by extension", () => {
    const f = makeFileWithSize("photo.jpg", 2048, "");
    expect(validateFile(f, "image")).toBeNull();
  });

  it("accepts a .png file by MIME", () => {
    const f = makeFileWithSize("icon.png", 2048, "image/png");
    expect(validateFile(f, "image")).toBeNull();
  });

  it("accepts a .webp file", () => {
    const f = makeFileWithSize("banner.webp", 2048, "image/webp");
    expect(validateFile(f, "image")).toBeNull();
  });

  it("rejects a .gif file (backend allow-list excludes GIF)", () => {
    const f = makeFileWithSize("anim.gif", 2048, "image/gif");
    expect(validateFile(f, "image")).not.toBeNull();
  });

  it("rejects a .svg file (backend allow-list excludes SVG to prevent XSS)", () => {
    const f = makeFileWithSize("logo.svg", 2048, "image/svg+xml");
    expect(validateFile(f, "image")).not.toBeNull();
  });

  it("rejects a .pdf file for image type", () => {
    const f = makeFileWithSize("drawing.pdf", 2048, "application/pdf");
    const result = validateFile(f, "image");
    expect(result).not.toBeNull();
    expect(result).toContain('"image"');
  });

  it("rejects an oversized image", () => {
    const f = makeFileWithSize("huge.jpg", MAX_BYTES + 1, "image/jpeg");
    const result = validateFile(f, "image");
    expect(result).not.toBeNull();
    expect(result).toContain("too large");
  });
});

// ---------------------------------------------------------------------------
// validateFile — invoice
// ---------------------------------------------------------------------------
describe("validateFile — invoice", () => {
  it("accepts a .pdf invoice", () => {
    const f = makeFileWithSize("inv.pdf", 512, "application/pdf");
    expect(validateFile(f, "invoice")).toBeNull();
  });

  it("accepts a scanned .png invoice", () => {
    const f = makeFileWithSize("inv.png", 512, "image/png");
    expect(validateFile(f, "invoice")).toBeNull();
  });

  it("accepts a .webp invoice", () => {
    const f = makeFileWithSize("inv.webp", 512, "image/webp");
    expect(validateFile(f, "invoice")).toBeNull();
  });

  it("rejects a .xlsx invoice (backend doesn't accept it)", () => {
    const f = makeFileWithSize("inv.xlsx", 512, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    const result = validateFile(f, "invoice");
    expect(result).not.toBeNull();
  });

  it("rejects a .mp4 file for invoice type", () => {
    const f = makeFileWithSize("video.mp4", 512, "video/mp4");
    const result = validateFile(f, "invoice");
    expect(result).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// validateFile — cad
// ---------------------------------------------------------------------------
describe("validateFile — cad", () => {
  // The backend only accepts PNG/JPEG/WebP/PDF, so the cad bucket is
  // limited to PDF (CAD drawing exports). Native CAD formats need a
  // backend allow-list expansion before the FE can advertise them.
  it("accepts a .pdf CAD export", () => {
    const f = makeFileWithSize("drawing.pdf", 1024, "application/pdf");
    expect(validateFile(f, "cad")).toBeNull();
  });

  it("rejects a .step file (backend allow-list excludes native CAD)", () => {
    const f = makeFileWithSize("part.step", 1024, "");
    expect(validateFile(f, "cad")).not.toBeNull();
  });

  it("rejects a .stl file (backend allow-list excludes native CAD)", () => {
    const f = makeFileWithSize("model.stl", 1024, "application/sla");
    expect(validateFile(f, "cad")).not.toBeNull();
  });

  it("rejects a .dxf file (backend allow-list excludes native CAD)", () => {
    const f = makeFileWithSize("drawing.dxf", 1024, "");
    expect(validateFile(f, "cad")).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// validateFile — bom
// ---------------------------------------------------------------------------
describe("validateFile — bom", () => {
  // The backend only accepts PNG/JPEG/WebP/PDF, so the bom bucket is
  // limited to PDF for now. Tabular BOM formats (.csv/.xlsx/.json) need
  // a backend allow-list expansion before the FE can advertise them.
  it("accepts a .pdf BOM", () => {
    const f = makeFileWithSize("bom.pdf", 512, "application/pdf");
    expect(validateFile(f, "bom")).toBeNull();
  });

  it("rejects a .csv BOM (backend allow-list excludes CSV)", () => {
    const f = makeFileWithSize("bom.csv", 512, "text/csv");
    expect(validateFile(f, "bom")).not.toBeNull();
  });

  it("rejects a .xlsx BOM (backend allow-list excludes XLSX)", () => {
    const f = makeFileWithSize("bom.xlsx", 512, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    expect(validateFile(f, "bom")).not.toBeNull();
  });

  it("rejects a .jpg for bom type", () => {
    const f = makeFileWithSize("photo.jpg", 512, "image/jpeg");
    const result = validateFile(f, "bom");
    expect(result).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// validateFile — other (permissive bucket)
// ---------------------------------------------------------------------------
describe("validateFile — other (permissive)", () => {
  it("accepts any extension", () => {
    expect(validateFile(makeFileWithSize("solidworks.SLDPRT", 1024, ""), "other")).toBeNull();
    expect(validateFile(makeFileWithSize("archive.tar.gz", 1024, ""), "other")).toBeNull();
    expect(validateFile(makeFileWithSize("no-ext", 1024, ""), "other")).toBeNull();
  });

  it("still rejects an oversized file even for type=other", () => {
    const f = makeFileWithSize("huge.bin", MAX_BYTES + 1, "");
    const result = validateFile(f, "other");
    expect(result).not.toBeNull();
    expect(result).toContain("too large");
  });
});

// ---------------------------------------------------------------------------
// ALLOWED_MIME_FOR_TYPE completeness
// ---------------------------------------------------------------------------
describe("ALLOWED_MIME_FOR_TYPE", () => {
  const ALL_TYPES = ["other", "datasheet", "invoice", "image", "cad", "bom"] as const;

  it("has an entry for every file_type dropdown value", () => {
    for (const t of ALL_TYPES) {
      expect(ALLOWED_MIME_FOR_TYPE[t]).toBeDefined();
    }
  });

  it("other has empty ext/mime lists (fully permissive)", () => {
    expect(ALLOWED_MIME_FOR_TYPE.other.exts).toHaveLength(0);
    expect(ALLOWED_MIME_FOR_TYPE.other.mimes).toHaveLength(0);
  });
});
