import { describe, expect, it } from "vitest";
import {
  PASSWORD_BLOCKLIST_MESSAGE,
  PASSWORD_REPETITIVE_MESSAGE,
  PASSWORD_TOO_SHORT_MESSAGE,
  getPasswordStrengthError,
} from "./passwordStrength";

describe("getPasswordStrengthError", () => {
  it("rejects passwords shorter than 8 characters", () => {
    expect(getPasswordStrengthError("short")).toBe(PASSWORD_TOO_SHORT_MESSAGE);
  });

  it("rejects backend mirrored static blocklist entries case-insensitively", () => {
    expect(getPasswordStrengthError("password")).toBe(PASSWORD_BLOCKLIST_MESSAGE);
    expect(getPasswordStrengthError("Password123")).toBe(PASSWORD_BLOCKLIST_MESSAGE);
    expect(getPasswordStrengthError("stockManager")).toBe(PASSWORD_BLOCKLIST_MESSAGE);
  });

  it("rejects repetitive low-variety passwords", () => {
    expect(getPasswordStrengthError("aaaaaaaa")).toBe(PASSWORD_REPETITIVE_MESSAGE);
    expect(getPasswordStrengthError("abababab")).toBe(PASSWORD_REPETITIVE_MESSAGE);
  });

  it("accepts passwords outside the best-effort frontend checks", () => {
    expect(getPasswordStrengthError("NewResetPass-2026!!")).toBeNull();
  });
});
