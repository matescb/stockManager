import { describe, expect, it } from "vitest";
import {
  isSafeHttpOrSameOriginUrl,
  isSafeHttpUrl,
  isSafeSameOriginPath,
} from "./url";

describe("isSafeHttpUrl", () => {
  it("test_isSafeHttpUrl_rejects_javascript", () => {
    expect(isSafeHttpUrl("javascript:alert(1)")).toBe(false);
  });

  it("rejects data URLs", () => {
    expect(isSafeHttpUrl("data:text/html,<script>alert(1)</script>")).toBe(false);
  });

  it("rejects non-http schemes and relative URLs", () => {
    expect(isSafeHttpUrl("mailto:ops@example.com")).toBe(false);
    expect(isSafeHttpUrl("/api/parts/assets/abc123")).toBe(false);
    expect(isSafeHttpUrl("//example.com/path")).toBe(false);
  });

  it("rejects whitespace and control character obfuscation", () => {
    expect(isSafeHttpUrl(" https://example.com")).toBe(false);
    expect(isSafeHttpUrl("java\nscript:alert(1)")).toBe(false);
    expect(isSafeHttpUrl("https://example.com/a b")).toBe(false);
  });

  it("accepts http and https URLs", () => {
    expect(isSafeHttpUrl("http://example.com/path")).toBe(true);
    expect(isSafeHttpUrl("https://example.com/path?x=1")).toBe(true);
    expect(isSafeHttpUrl("HTTPS://example.com/path")).toBe(true);
  });
});

describe("isSafeSameOriginPath", () => {
  it("accepts same-origin app paths", () => {
    expect(isSafeSameOriginPath("/api/parts/assets/abc123")).toBe(true);
    expect(isSafeSameOriginPath("/img/stm32.png")).toBe(true);
  });

  it("rejects protocol-relative and unsafe paths", () => {
    expect(isSafeSameOriginPath("//evil.example/path")).toBe(false);
    expect(isSafeSameOriginPath("/api/parts/assets/a b")).toBe(false);
  });
});

describe("isSafeHttpOrSameOriginUrl", () => {
  it("accepts http URLs and same-origin paths only", () => {
    expect(isSafeHttpOrSameOriginUrl("https://example.com")).toBe(true);
    expect(isSafeHttpOrSameOriginUrl("/api/attachments/1/download")).toBe(true);
    expect(isSafeHttpOrSameOriginUrl("data:image/svg+xml,<svg></svg>")).toBe(false);
  });
});
