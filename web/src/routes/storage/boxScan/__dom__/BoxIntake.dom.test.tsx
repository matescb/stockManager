import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { apiGet, apiPost, FakeApiError, RAW_A, RAW_NO_1P } from "./mocks";

vi.mock("@/instrument", () => ({}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ workspaceId: "ws-1" }) }));
vi.mock("@/lib/queryKeys", () => ({ useWsKey: (...a: unknown[]) => ["ws-1", ...a] }));
vi.mock("@/components/scanner/Scanner", () => ({ default: () => <div data-testid="camera" /> }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/api", async () => {
  const m = await import("./mocks");
  return { api: { get: m.apiGet, post: m.apiPost }, ApiError: m.FakeApiError };
});

import BoxIntake from "../BoxIntake";

const part = { id: "p1", name: "LM358", mpn: "LM358", manufacturer: "TI", description: null };

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/storage/box1/intake"]}>
        <Routes>
          <Route path="/storage/:storageId/intake" element={<BoxIntake />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function scan(raw: string) {
  fireEvent.change(screen.getByPlaceholderText("Paste or type a bag code"), { target: { value: raw } });
  fireEvent.click(screen.getByRole("button", { name: "Scan" }));
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  apiGet.mockReset();
  apiPost.mockReset();
});
afterEach(cleanup);

describe("BoxIntake", () => {
  it("adds a scanned bag of an existing part immediately with the label quantity", async () => {
    apiPost.mockImplementation((url: string) =>
      url.endsWith("/scan/resolve")
        ? Promise.resolve({ status: "existing_part", part, bag: null, provider: null })
        : Promise.resolve({ status: "added", part, part_created: false, lot_id: "l1", quantity: 100, moved_from: [] }),
    );
    mount();
    scan(RAW_A);

    expect(await screen.findByText("Added")).toBeTruthy();
    const intakeCall = apiPost.mock.calls.find(c => c[0] === "/storage/box1/intake");
    expect(intakeCall?.[1]).toMatchObject({ part_id: "p1", quantity: 100, lot_name: "Lot AAA" });
    expect(intakeCall?.[1].bag_signature).toMatch(/^[a-f0-9]{64}$/);
  });

  it("asks before moving a bag recorded in another box", async () => {
    apiPost.mockImplementation((url: string) =>
      url.endsWith("/scan/resolve")
        ? Promise.resolve({
            status: "bag_elsewhere",
            part,
            bag: { lot_id: "l9", lot_name: "x", here: false, quantity_here: 0, locations: [{ storage_location_id: "b2", name: "Old box", quantity: 80 }] },
            provider: null,
          })
        : Promise.resolve({ status: "moved", part, part_created: false, lot_id: "l9", quantity: 100, moved_from: [] }),
    );
    mount();
    scan(RAW_A);

    expect(await screen.findByText("in Old box (80)")).toBeTruthy();
    expect(apiPost.mock.calls.some(c => c[0] === "/storage/box1/intake")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /Move here/ }));
    expect(await screen.findByText("Moved here")).toBeTruthy();
    expect(apiPost.mock.calls.find(c => c[0] === "/storage/box1/intake")?.[1]).toMatchObject({ on_existing: "move" });
  });

  it("adds a part by MPN and quantity", async () => {
    apiPost.mockImplementation((url: string) =>
      url.endsWith("/scan/resolve")
        ? Promise.resolve({ status: "provider_match", part: null, bag: null, provider: { mpn: "NE555", manufacturer: "TI", description: null } })
        : Promise.resolve({ status: "added", part: { ...part, name: "NE555" }, part_created: true, lot_id: "l2", quantity: 7, moved_from: [] }),
    );
    mount();
    fireEvent.change(screen.getByPlaceholderText("MPN"), { target: { value: "NE555" } });
    fireEvent.change(screen.getByPlaceholderText("Qty"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: /Add by MPN/ }));

    expect(await screen.findByText("new part")).toBeTruthy();
    expect(apiPost.mock.calls.find(c => c[0] === "/storage/box1/intake")?.[1]).toEqual({
      quantity: 7, on_existing: "move", mpn: "NE555",
    });
  });

  it("corrects a saved count through stock/adjust", async () => {
    apiPost.mockImplementation((url: string) => {
      if (url.endsWith("/scan/resolve")) return Promise.resolve({ status: "existing_part", part, bag: null, provider: null });
      if (url.endsWith("/intake")) return Promise.resolve({ status: "added", part, part_created: false, lot_id: "l1", quantity: 100, moved_from: [] });
      return Promise.resolve(null);
    });
    mount();
    scan(RAW_A);
    await screen.findByText("Added");

    fireEvent.change(screen.getByLabelText("Bag quantity"), { target: { value: "95" } });
    fireEvent.click(screen.getByRole("button", { name: "Update count" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/stock/adjust", expect.objectContaining({
      part_id: "p1", lot_id: "l1", storage_location_id: "box1", actual_quantity: 95,
    })));
  });

  it("shows the error when the part cannot be found", async () => {
    apiPost.mockImplementation((url: string) =>
      url.endsWith("/scan/resolve")
        ? Promise.resolve({ status: "not_found", part: null, bag: null, provider: null })
        : Promise.reject(new FakeApiError(404)),
    );
    mount();
    scan(RAW_A);
    expect(await screen.findByText(/not in the library/)).toBeTruthy();
    expect(screen.getByText("Create part…")).toBeTruthy();
  });

  describe("bag label without a manufacturer part number", () => {
    const murata = { ...part, id: "p2", name: "GRM188", mpn: "GRM188R71H104KA93D", manufacturer: "Murata" };

    function mockResolve() {
      apiPost.mockImplementation((url: string, body: { mpn?: string }) => {
        if (url.endsWith("/scan/resolve")) {
          return Promise.resolve(
            body.mpn
              ? { status: "existing_part", part: murata, bag: null, provider: null }
              : { status: "not_found", part: null, bag: null, provider: null },
          );
        }
        return Promise.resolve({ status: "added", part: murata, part_created: false, lot_id: "l3", quantity: 100, moved_from: [] });
      });
    }

    it("explains the missing MPN instead of a provider miss, and never sends the raw code as MPN", async () => {
      mockResolve();
      mount();
      scan(RAW_NO_1P);

      expect(await screen.findByText(/has no manufacturer part number \(Murata · qty 100 · customer P\/N C201,C202,C503\)/)).toBeTruthy();
      expect(screen.queryByText(/not in the library/)).toBeNull();
      const resolveCall = apiPost.mock.calls.find(c => c[0] === "/storage/box1/scan/resolve");
      expect(resolveCall?.[1].mpn).toBe("");
      expect(resolveCall?.[1].raw_bag_code).toBe(RAW_NO_1P);
    });

    it("saves with the label's quantity and order refs once the MPN is typed", async () => {
      mockResolve();
      mount();
      scan(RAW_NO_1P);
      await screen.findByText(/has no manufacturer part number/);

      fireEvent.change(screen.getByPlaceholderText("Mfr. part number"), { target: { value: "GRM188R71H104KA93D" } });
      fireEvent.click(screen.getByRole("button", { name: /Look up/ }));

      expect(await screen.findByText("Added")).toBeTruthy();
      expect(apiPost.mock.calls.find(c => c[0] === "/storage/box1/intake")?.[1]).toMatchObject({
        part_id: "p2",
        quantity: 100,
        comments: "Order 40489709 · line 027 · invoice 092571642",
        raw_bag_code: RAW_NO_1P,
      });
    });

    it("completes the waiting label with the next bare part-number scan", async () => {
      mockResolve();
      mount();
      scan(RAW_NO_1P);
      await screen.findByText(/has no manufacturer part number/);

      scan("GRM188R71H104KA93D");

      expect(await screen.findByText("Added")).toBeTruthy();
      expect(screen.getAllByTestId("intake-row")).toHaveLength(1);
      expect(apiPost.mock.calls.find(c => c[0] === "/storage/box1/intake")?.[1]).toMatchObject({ quantity: 100 });
    });
  });
});
