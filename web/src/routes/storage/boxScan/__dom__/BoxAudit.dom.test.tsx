import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { bagSignature } from "@/lib/bagCode";
import { apiGet, apiPost, RAW_A, RAW_B, RAW_NO_1P } from "./mocks";

vi.mock("@/instrument", () => ({}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ workspaceId: "ws-1" }) }));
vi.mock("@/lib/queryKeys", () => ({ useWsKey: (...a: unknown[]) => ["ws-1", ...a] }));
vi.mock("@/components/scanner/Scanner", () => ({ default: () => <div data-testid="camera" /> }));
vi.mock("@/components/ConfirmDialog", () => ({ useConfirm: () => async () => true }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn(), success: vi.fn() } }));
vi.mock("@/components/scanner/scanSounds", () => ({
  playScanSound: vi.fn(),
  isScanSoundMuted: () => false,
  setScanSoundMuted: vi.fn(),
  primeScanAudio: () => () => {},
}));
vi.mock("@/lib/api", async () => {
  const m = await import("./mocks");
  return { api: { get: m.apiGet, post: m.apiPost }, ApiError: m.FakeApiError };
});

import BoxAudit from "../BoxAudit";

const part = { id: "p1", name: "LM358", mpn: "LM358", manufacturer: "TI", description: null };

async function setup() {
  const [sa, sb] = [await bagSignature(RAW_A), await bagSignature(RAW_B)];
  apiGet.mockImplementation((url: string) =>
    url === "/storage/box1/bags"
      ? Promise.resolve([
          { part, lot: { id: "la", name: "Lot AAA" }, quantity: 100, bag_signatures: [sa] },
          { part, lot: { id: "lb", name: "Lot BBB" }, quantity: 50, bag_signatures: [sb] },
          { part, lot: null, quantity: 3, bag_signatures: [] },
        ])
      : Promise.resolve([{ id: "box2", name: "Unsorted" }]),
  );
  apiPost.mockResolvedValue({ count: 1, write_off: 2, move: 0, unchanged: 0 });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/storage/box1/audit"]}>
        <Routes>
          <Route path="/storage/:storageId/audit" element={<BoxAudit />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("Not scanned (3)");
}

function scan(raw: string) {
  fireEvent.change(screen.getByPlaceholderText("Paste or type a bag code"), { target: { value: raw } });
  fireEvent.click(screen.getByRole("button", { name: "Scan" }));
}

beforeEach(() => {
  sessionStorage.clear();
  apiGet.mockReset();
  apiPost.mockReset();
});
afterEach(cleanup);

describe("BoxAudit", () => {
  it("ticks off scanned bags and lists the rest as not scanned", async () => {
    await setup();
    scan(RAW_A);

    expect(await screen.findByText("Scanned (1)")).toBeTruthy();
    expect(screen.getByText("Not scanned (2)")).toBeTruthy();
    expect(screen.getByText(/old record, not tracked per bag/)).toBeTruthy();
    expect(apiPost).not.toHaveBeenCalled(); // a recorded bag needs no server round trip
  });

  it("applies counts, write-offs and moves on finish", async () => {
    await setup();
    scan(RAW_A);
    await screen.findByText("Scanned (1)");
    fireEvent.change(screen.getByLabelText("Counted quantity"), { target: { value: "90" } });
    const notScanned = screen.getByText("Not scanned (2)").closest("section")!;
    const [firstAction] = within(notScanned).getAllByLabelText("Action");
    fireEvent.change(firstAction, { target: { value: "move" } });
    fireEvent.change(await within(notScanned).findByLabelText("Destination box"), { target: { value: "box2" } });

    fireEvent.click(screen.getByRole("button", { name: "Finish audit" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [url, body] = apiPost.mock.calls[0];
    expect(url).toBe("/storage/box1/audit");
    expect(body.lines).toEqual(expect.arrayContaining([
      { part_id: "p1", lot_id: "la", action: "count", actual_quantity: 90 },
      { part_id: "p1", lot_id: "lb", action: "move", to_storage_location_id: "box2" },
      { part_id: "p1", lot_id: null, action: "write_off" },
    ]));
    expect(body.lines).toHaveLength(3);
  });

  it("offers to add a bag that is not recorded in this box", async () => {
    await setup();
    apiPost.mockImplementation((url: string) =>
      url.endsWith("/scan/resolve")
        ? Promise.resolve({ status: "existing_part", part, bag: null, provider: null })
        : Promise.resolve({ status: "added", part, part_created: false, lot_id: "lc", quantity: 10, moved_from: [] }),
    );
    scan("[)>\x1e06\x1d1PLM358\x1dQ10\x1d1TCCC\x1e\x04");

    expect(await screen.findByText(/Found here, not recorded/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Add to box/ }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/storage/box1/intake", expect.objectContaining({ part_id: "p1", quantity: 10 })));
  });

  it("asks for the MPN of an unrecorded bag whose label has none, via a follow-up scan", async () => {
    await setup();
    apiPost.mockImplementation((url: string, body: { mpn?: string }) =>
      url.endsWith("/scan/resolve")
        ? Promise.resolve(body.mpn ? { status: "existing_part", part, bag: null, provider: null } : { status: "not_found", part: null, bag: null, provider: null })
        : Promise.resolve({ status: "added", part, part_created: false, lot_id: "lm", quantity: 100, moved_from: [] }),
    );
    scan(RAW_NO_1P);

    expect((await screen.findByTestId("follow-up-prompt")).textContent).toContain("Scan the Mfr. Part No barcode on this label (Murata · qty 100");
    expect(screen.getByText(/has no manufacturer part number/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /Add to box/ }) as HTMLButtonElement).disabled).toBe(true);
    scan("LM358");
    await screen.findByText(/Part is in the library/);
    expect(screen.queryByTestId("follow-up-prompt")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Add to box/ }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/storage/box1/intake", expect.objectContaining({ part_id: "p1", quantity: 100 })));
  });
});
