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
const sounds = vi.hoisted(() => ({
  playScanSound: vi.fn(),
  playScanBeep: vi.fn(),
  isScanSoundMuted: () => false,
  setScanSoundMuted: vi.fn(),
  primeScanAudio: () => () => {},
}));
vi.mock("@/components/scanner/scanSounds", () => sounds);
vi.mock("@/lib/api", async () => {
  const m = await import("./mocks");
  return { api: { get: m.apiGet, post: m.apiPost }, ApiError: m.FakeApiError };
});

import { bagSignature } from "@/lib/bagCode";
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
  sounds.playScanSound.mockReset();
  sounds.setScanSoundMuted.mockReset();
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

  describe("scan sounds", () => {
    it("beeps success on a resolved scan", async () => {
      apiPost.mockImplementation((url: string) =>
        url.endsWith("/scan/resolve")
          ? Promise.resolve({ status: "existing_part", part, bag: null, provider: null })
          : Promise.resolve({ status: "added", part, part_created: false, lot_id: "l1", quantity: 100, moved_from: [] }),
      );
      mount();
      scan(RAW_A);
      await screen.findByText("Added");
      expect(sounds.playScanSound.mock.calls).toEqual([["success"]]);
    });

    it("buzzes error when the part cannot be found", async () => {
      apiPost.mockResolvedValue({ status: "not_found", part: null, bag: null, provider: null });
      mount();
      scan(RAW_A);
      await screen.findByText(/not in the library/);
      expect(sounds.playScanSound.mock.calls).toEqual([["error"]]);
    });

    it("has a mute toggle that is remembered", () => {
      mount();
      fireEvent.click(screen.getByRole("button", { name: /Sound on/ }));
      expect(sounds.setScanSoundMuted).toHaveBeenCalledWith(true);
      expect(screen.getByRole("button", { name: /Sound off/ }).getAttribute("aria-pressed")).toBe("true");
    });
  });

  describe("follow-up scan for a label that lacks a field", () => {
    const murata = { ...part, id: "p2", name: "GRM188", mpn: "GRM188R71H104KA93D", manufacturer: "Murata" };
    const PROMPT = "Scan the Mfr. Part No barcode on this label (Murata · qty 100 · Mouser order 40489709 line 027)";

    function mockResolve() {
      apiPost.mockImplementation((url: string, body: { mpn?: string }) => {
        if (url.endsWith("/scan/resolve")) {
          return Promise.resolve(
            body.mpn
              ? { status: "existing_part", part: body.mpn === "LM358" ? part : murata, bag: null, provider: null }
              : { status: "not_found", part: null, bag: null, provider: null },
          );
        }
        return Promise.resolve({ status: "added", part: murata, part_created: false, lot_id: "l3", quantity: 100, moved_from: [] });
      });
    }

    const intakeBodies = () => apiPost.mock.calls.filter(c => c[0] === "/storage/box1/intake").map(c => c[1]);

    it("waits for the Mfr. Part No with a distinct sound, never sending the raw code as MPN", async () => {
      mockResolve();
      mount();
      scan(RAW_NO_1P);

      expect((await screen.findByTestId("follow-up-prompt")).textContent).toContain(PROMPT);
      expect(sounds.playScanSound.mock.calls).toEqual([["needs_info"]]);
      expect(screen.queryByText(/not in the library/)).toBeNull();
      const resolveCall = apiPost.mock.calls.find(c => c[0] === "/storage/box1/scan/resolve");
      expect(resolveCall?.[1].mpn).toBe("");
    });

    it("takes the next plain scan as the MPN and keeps the original label's traceability and signature", async () => {
      mockResolve();
      mount();
      scan(RAW_NO_1P);
      await screen.findByTestId("follow-up-prompt");

      scan("GRM188R71H104KA93D");

      expect(await screen.findByText("Added")).toBeTruthy();
      expect(screen.queryByTestId("follow-up-prompt")).toBeNull();
      expect(screen.getAllByTestId("intake-row")).toHaveLength(1);
      expect(sounds.playScanSound.mock.calls).toEqual([["needs_info"], ["success"]]);
      expect(intakeBodies()).toEqual([
        expect.objectContaining({
          part_id: "p2",
          quantity: 100,
          comments: "Order 40489709 · line 027 · invoice 092571642",
          raw_bag_code: RAW_NO_1P,
          bag_signature: await bagSignature(RAW_NO_1P),
        }),
      ]);
    });

    it("cancels with the button and with Esc; the bag keeps its inline MPN box", async () => {
      mockResolve();
      mount();
      scan(RAW_NO_1P);
      await screen.findByTestId("follow-up-prompt");
      fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));
      expect(screen.queryByTestId("follow-up-prompt")).toBeNull();
      expect(screen.getByPlaceholderText("Mfr. part number")).toBeTruthy();

      scan("[)>06K4048970914K028PC102,C103Q10011K0925716424LJP1VMurata");
      await screen.findByTestId("follow-up-prompt");
      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByTestId("follow-up-prompt")).toBeNull();
    });

    it("abandons the prompt when another 2D label is scanned, and handles that label as its own bag", async () => {
      mockResolve();
      mount();
      scan(RAW_NO_1P);
      await screen.findByTestId("follow-up-prompt");

      scan(RAW_A);

      await waitFor(() => expect(screen.getAllByTestId("intake-row")).toHaveLength(2));
      expect(screen.queryByTestId("follow-up-prompt")).toBeNull();
      expect(intakeBodies()).toEqual([expect.objectContaining({ part_id: "p1", quantity: 100 })]);
    });

    it("asks for the quantity when the label has none, and saves the typed answer", async () => {
      mockResolve();
      mount();
      scan("[)>\x1e06\x1d1PLM358\x1d1TQQQ\x1e\x04");

      expect((await screen.findByTestId("follow-up-prompt")).textContent).toContain("Scan or type the quantity");
      expect(sounds.playScanSound.mock.calls).toEqual([["needs_info"]]);
      fireEvent.change(screen.getByPlaceholderText("or type the quantity"), { target: { value: "25" } });
      fireEvent.click(screen.getByRole("button", { name: "Use" }));

      await waitFor(() => expect(intakeBodies()).toEqual([expect.objectContaining({ part_id: "p1", quantity: 25 })]));
    });
  });
});
