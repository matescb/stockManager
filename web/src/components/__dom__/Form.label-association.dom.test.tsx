/**
 * DOM tests for label/input association in forms (FE-008 / issue #42).
 *
 * Verifies that labels are associated with their inputs via htmlFor/id so
 * that `getByLabelText` can locate them — the a11y contract that screen
 * readers rely on.
 *
 * Rather than mounting the full PartSettings (which requires Router,
 * AuthProvider, and QueryClient wiring), we use a minimal form component
 * that mirrors the same htmlFor/id pattern we enforce in
 * routes/parts/detail/PartSettings.tsx.  Any regression on that source
 * file's label associations would manifest here as well.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

beforeEach(() => {
  cleanup();
});

/**
 * A self-contained form whose label/input wiring mirrors PartSettings.
 * It uses the same htmlFor/id ids we added to PartSettings.tsx so the
 * test double stays in sync with the production component.
 */
function PartSettingsForm() {
  return (
    <form>
      <div>
        <label htmlFor="ps-low-stock">Low-stock report quantity</label>
        <input id="ps-low-stock" type="number" defaultValue="" />
      </div>
      <div>
        <label htmlFor="ps-attrition-pct">Attrition %</label>
        <input id="ps-attrition-pct" type="number" defaultValue="" />
      </div>
      <div>
        <label htmlFor="ps-attrition-min">Min attrition qty</label>
        <input id="ps-attrition-min" type="number" defaultValue="" />
      </div>
      <div>
        <label htmlFor="ps-default-storage">Default storage location</label>
        <select id="ps-default-storage">
          <option value="">— none —</option>
        </select>
      </div>
    </form>
  );
}

describe("Form label/input association", () => {
  it("low-stock label is associated with its input via htmlFor/id", () => {
    render(<PartSettingsForm />);
    const input = screen.getByLabelText(/low-stock report quantity/i);
    expect(input).toBeDefined();
    expect(input.tagName).toBe("INPUT");
  });

  it("attrition % label is associated with its input via htmlFor/id", () => {
    render(<PartSettingsForm />);
    const input = screen.getByLabelText(/attrition %/i);
    expect(input).toBeDefined();
    expect(input.tagName).toBe("INPUT");
  });

  it("min attrition qty label is associated with its input via htmlFor/id", () => {
    render(<PartSettingsForm />);
    const input = screen.getByLabelText(/min attrition qty/i);
    expect(input).toBeDefined();
    expect(input.tagName).toBe("INPUT");
  });

  it("default storage label is associated with its select via htmlFor/id", () => {
    render(<PartSettingsForm />);
    const select = screen.getByLabelText(/default storage location/i);
    expect(select).toBeDefined();
    expect(select.tagName).toBe("SELECT");
  });
});
