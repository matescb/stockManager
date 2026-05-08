/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PoweredByTrustedParts } from "../PoweredByTrustedParts";

afterEach(() => {
  cleanup();
});

describe("PoweredByTrustedParts", () => {
  it("renders a followable link without rel='nofollow'", () => {
    render(<PoweredByTrustedParts primaryUrl="https://example.com/trustedparts" />);

    const link = screen.getByRole("link", { name: "Powered by TrustedParts" });

    expect(link.getAttribute("href")).toBe("https://example.com/trustedparts");
    expect(link.getAttribute("rel")).not.toContain("nofollow");
  });

  it("falls back to https://www.trustedparts.com when primaryUrl is undefined", () => {
    render(<PoweredByTrustedParts />);

    const link = screen.getByRole("link", { name: "Powered by TrustedParts" });

    expect(link.getAttribute("href")).toBe("https://www.trustedparts.com");
  });

  it("opens in a new tab with rel='noopener noreferrer'", () => {
    render(<PoweredByTrustedParts />);

    const link = screen.getByRole("link", { name: "Powered by TrustedParts" });

    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("respects the className prop", () => {
    render(<PoweredByTrustedParts className="ml-2" />);

    const link = screen.getByRole("link", { name: "Powered by TrustedParts" });

    expect(link.className).toContain("pill");
    expect(link.className).toContain("ml-2");
  });
});
