import { describe, it, expect } from "vitest";
import { formatDate, formatDateTime, formatMoney } from "./format";

describe("formatDate", () => {
  it("returns YYYY-MM-DD for a valid ISO string", () => {
    expect(formatDate("2024-03-15T10:30:00Z")).toBe("2024-03-15");
  });

  it("returns '' for null", () => {
    expect(formatDate(null)).toBe("");
  });

  it("returns '' for undefined", () => {
    expect(formatDate(undefined)).toBe("");
  });

  it("returns '' for empty string", () => {
    expect(formatDate("")).toBe("");
  });

  it("returns '' for invalid date string", () => {
    expect(formatDate("not-a-date")).toBe("");
  });
});

describe("formatDateTime", () => {
  it("returns a non-empty string for a valid ISO datetime", () => {
    const result = formatDateTime("2024-03-15T10:30:00Z");
    expect(result).toBeTruthy();
    expect(typeof result).toBe("string");
  });

  it("returns '' for null", () => {
    expect(formatDateTime(null)).toBe("");
  });

  it("returns '' for undefined", () => {
    expect(formatDateTime(undefined)).toBe("");
  });

  it("returns '' for empty string", () => {
    expect(formatDateTime("")).toBe("");
  });

  it("returns '' for invalid date string", () => {
    expect(formatDateTime("not-a-date")).toBe("");
  });
});

describe("formatMoney", () => {
  it("formats USD with 2 decimal places", () => {
    const result = formatMoney(12.5, "USD");
    expect(result).toContain("12.50");
    expect(result).toContain("$");
  });

  it("formats JPY with 0 decimal places", () => {
    const result = formatMoney(1500, "JPY");
    expect(result).not.toContain(".");
    expect(result).toContain("1,500");
  });

  it("formats EUR with 2 decimal places", () => {
    const result = formatMoney(9.99, "EUR");
    expect(result).toContain("9.99");
  });

  it("falls back for empty currency", () => {
    expect(formatMoney(5.5, "")).toBe("5.50");
  });

  it("falls back for null currency", () => {
    expect(formatMoney(5.5, null)).toBe("5.50");
  });

  it("falls back for undefined currency", () => {
    expect(formatMoney(5.5, undefined)).toBe("5.50");
  });

  it("falls back for invalid ISO 4217 code", () => {
    const result = formatMoney(10, "INVALID");
    expect(result).toContain("10.00");
    expect(result).toContain("INVALID");
  });
});
