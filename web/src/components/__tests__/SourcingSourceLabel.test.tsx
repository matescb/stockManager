/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourcingSourceLabel } from "../SourcingSourceLabel";

afterEach(() => {
  cleanup();
});

describe("SourcingSourceLabel", () => {
  it("renders TrustedParts label for source='trustedparts'", () => {
    render(<SourcingSourceLabel source="trustedparts" />);

    const label = screen.getByText("TrustedParts");

    expect(label.className).toContain("pill");
  });

  it("attaches aria-label for accessibility", () => {
    render(<SourcingSourceLabel source="trustedparts" />);

    const label = screen.getByLabelText("Source: TrustedParts");

    expect(label.textContent).toBe("TrustedParts");
  });

  it("throws for an unknown source", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    try {
      expect(() => {
        render(<SourcingSourceLabel source={"mouser" as "trustedparts"} />);
      }).toThrow("Unsupported sourcing source: mouser");
    } finally {
      consoleError.mockRestore();
    }
  });
});
