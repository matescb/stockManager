import { describe, expect, it } from "vitest";
import { ApiError, getPolicyViolation, type ApiErr } from "../api";

const missing = [
  { field: "manufacturer", rule_id: "mfr", when: "create", message: "Manufacturer is required", hint: "from the datasheet" },
];

function apiError(status: number, extra: Record<string, unknown>): ApiError {
  const body = { data: null, status: { category: "validation_error", message: "x" }, ...extra } as ApiErr;
  return new ApiError(status, body, "x");
}

describe("getPolicyViolation", () => {
  it("returns the top-level missing list of a 422 part.policy_violation", () => {
    expect(getPolicyViolation(apiError(422, { code: "part.policy_violation", missing }))).toEqual(missing);
  });

  it("ignores other 422s, other statuses and non-ApiErrors", () => {
    expect(getPolicyViolation(apiError(422, { code: "part.name_or_mpn_required" }))).toBeNull();
    expect(getPolicyViolation(apiError(409, { code: "part.policy_violation", missing }))).toBeNull();
    expect(getPolicyViolation(new Error("boom"))).toBeNull();
  });
});
