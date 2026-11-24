/** ScanImportSession: a bag label without a 1P never reaches the provider. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("@/components/scanner/Scanner", () => ({ default: () => <div data-testid="camera" /> }));
vi.mock("@/lib/api", () => ({
  api: { get: (...a: unknown[]) => apiGet(...a), post: (...a: unknown[]) => apiPost(...a) },
  ApiError: class extends Error {},
}));

import ScanImportSession from "../ScanImportSession";

const RAW_NO_1P = "[)>06K4048970914K027PC201,C202,C503Q10011K0925716424LJP1VMurata";

function mount() {
  const onRow = vi.fn();
  const onLookupUpdate = vi.fn();
  render(
    <ScanImportSession
      seenSigs={{ current: new Set() }}
      seenMpns={{ current: new Set() }}
      onRow={onRow}
      onLookupUpdate={onLookupUpdate}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Manual entry/ }));
  fireEvent.change(screen.getByPlaceholderText("Paste bag code"), { target: { value: RAW_NO_1P } });
  fireEvent.click(screen.getByRole("button", { name: /Add bag/ }));
  return { onRow, onLookupUpdate };
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
});
afterEach(cleanup);

describe("ScanImportSession — label without a manufacturer part number", () => {
  it("adds a row that explains the missing MPN and skips the provider lookup", async () => {
    apiGet.mockResolvedValue(null); // bag not seen before
    const { onRow, onLookupUpdate } = mount();

    await waitFor(() => expect(onLookupUpdate).toHaveBeenCalled());
    expect(onRow.mock.calls[0][0].bag).toMatchObject({ mpn: "", noMpn: true, quantity: 100 });
    const [, state] = onLookupUpdate.mock.calls[0];
    expect(state.kind).toBe("error");
    expect(state.message).toMatch(/no manufacturer part number \(Murata · qty 100/);
    expect(apiPost).not.toHaveBeenCalled();
    expect(apiGet.mock.calls.every(c => String(c[0]).startsWith("/parts/by-bag-signature/"))).toBe(true);
  });

  it("still offers quick-remove when the bag is already in stock", async () => {
    apiGet.mockResolvedValue({ part_id: "p1", lot_id: "l1", storage_location_id: null, quantity: 80 });
    const { onLookupUpdate } = mount();

    await waitFor(() => expect(onLookupUpdate).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ kind: "bag_rescan", quantity: 80 })));
    expect(apiPost).not.toHaveBeenCalled();
  });
});
