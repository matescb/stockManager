// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { api } from "@/lib/api";
import type { Part } from "@/types";
import PartCad from "../PartCad";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const PART_ID = "11111111-1111-4111-8111-111111111111";
const SYMBOL_ID = "22222222-2222-4222-8222-222222222222";
const FOOTPRINT_ID = "33333333-3333-4333-8333-333333333333";
const STEP_ID = "44444444-4444-4444-8444-444444444444";
const SPICE_ID = "55555555-5555-4555-8555-555555555555";

const part: Part = {
  id: PART_ID,
  part_type: "local",
  name: "Resistor 10k",
  manufacturer: null,
  mpn: null,
  internal_part_number: null,
  description: null,
  footprint: null,
  notes_markdown: null,
  low_stock_report_quantity: null,
  attrition_percentage: 0,
  attrition_min_quantity: 0,
  default_storage_location_id: null,
  default_storage_mandatory: false,
  serialized: false,
  category_id: null,
  linked_provider: null,
  linked_external_id: null,
  last_refresh_at: null,
  description_locally_edited: false,
  archived_at: null,
  on_hand: 0,
  reserved: 0,
  available: 0,
  image_url: null,
};

const symbols = [
  {
    id: SYMBOL_ID,
    name: "R",
    sha256: "a".repeat(64),
    size_bytes: 120,
    source: "manual",
    category_id: null,
    archived_at: null,
  },
];

const footprints = [
  {
    id: FOOTPRINT_ID,
    name: "R_0402_1005Metric",
    sha256: "b".repeat(64),
    size_bytes: 240,
    source: "manual",
    category_id: null,
    archived_at: null,
  },
];

const datafiles = [
  {
    id: STEP_ID,
    kind: "step",
    name: "R_0402.step",
    sha256: "c".repeat(64),
    size_bytes: 900,
    source: "manual",
    archived_at: null,
  },
  {
    id: SPICE_ID,
    kind: "spice",
    name: "resistor.lib",
    sha256: "d".repeat(64),
    size_bytes: 60,
    source: "manual",
    archived_at: null,
  },
];

const emptyConfig = null;

/**
 * `PartCad` reads four endpoints through `api.parsed.get`. Route by path
 * so each test only has to say what's different, and so a component that
 * starts fetching something new fails loudly rather than silently
 * receiving the wrong fixture.
 */
function mockReads(config: unknown = emptyConfig) {
  return vi.spyOn(api.parsed, "get").mockImplementation((path: string) => {
    // The list queries pass ?limit=1000; route on the bare path so the
    // fixtures still match.
    const bare = path.split("?")[0];
    if (bare === `/parts/${PART_ID}/eda`) return Promise.resolve(config);
    if (bare === "/eda/symbols") return Promise.resolve(symbols);
    if (bare === "/eda/footprints") return Promise.resolve(footprints);
    if (bare === "/eda/datafiles") return Promise.resolve(datafiles);
    if (bare === `/eda/footprints/${FOOTPRINT_ID}/models`) return Promise.resolve([]);
    throw new Error(`unexpected GET ${path}`);
  });
}

function renderPartCad() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/parts/${PART_ID}/cad`]}>
        <Routes>
          <Route path="/parts/:partId" element={<Outlet context={{ part }} />}>
            <Route path="cad" element={<PartCad />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("PartCad", () => {
  it("renders the four sections and defaults every slot to the category fallback", async () => {
    mockReads();
    renderPartCad();

    expect(await screen.findByText("Symbol")).toBeDefined();
    expect(screen.getByText("Footprint")).toBeDefined();
    expect(screen.getByText("3D models")).toBeDefined();
    expect(screen.getByText("Simulation (SPICE)")).toBeDefined();
    expect(screen.getByText("Schematic fields")).toBeDefined();

    // No config yet → "None (category default)" on both slots, so neither
    // the hosted select nor the external input is showing.
    expect(screen.queryByLabelText("Symbol from this workspace")).toBeNull();
    expect(screen.queryByLabelText("KiCad library reference")).toBeNull();
    expect(
      screen.getByText("Select a hosted footprint to attach 3D models to it."),
    ).toBeDefined();
  });

  it("seeds the form from an existing configuration", async () => {
    mockReads({
      part_id: PART_ID,
      symbol_id: SYMBOL_ID,
      symbol_ref_external: null,
      footprint_id: null,
      footprint_ref_external: "Resistor_SMD:R_0402_1005Metric",
      spice_datafile_id: SPICE_ID,
      value: "10k",
      keywords: "resistor smd",
      footprint_filters: ["R_*", "*_0402_*"],
      exclude_from_bom: true,
      exclude_from_board: false,
      exclude_from_sim: false,
      sim_device: "R",
      sim_pins: "1=+ 2=-",
      sim_params: "r=10k",
    });
    renderPartCad();

    const symbolSelect = (await screen.findByLabelText(
      "Symbol from this workspace",
    )) as HTMLSelectElement;
    expect(symbolSelect.value).toBe(SYMBOL_ID);

    // The footprint slot took the external branch, so it shows the text
    // input rather than the select — the two are mutually exclusive.
    const external = screen.getByLabelText("KiCad library reference") as HTMLInputElement;
    expect(external.value).toBe("Resistor_SMD:R_0402_1005Metric");

    expect((screen.getByLabelText("Value") as HTMLInputElement).value).toBe("10k");
    expect((screen.getByLabelText("Footprint filters") as HTMLInputElement).value).toBe(
      "R_*, *_0402_*",
    );
    expect((screen.getByLabelText("Model file") as HTMLSelectElement).value).toBe(SPICE_ID);
  });

  it("saves one half of each slot and splits the filter list", async () => {
    mockReads();
    const put = vi.spyOn(api, "put").mockResolvedValue({});
    renderPartCad();

    // Symbol → hosted, footprint → external. The payload must carry only
    // the chosen half of each; sending both is a 422 server-side.
    fireEvent.click((await screen.findAllByLabelText("Hosted here"))[0]);
    fireEvent.change(screen.getByLabelText("Symbol from this workspace"), {
      target: { value: SYMBOL_ID },
    });
    fireEvent.click(screen.getAllByLabelText("External reference")[1]);
    fireEvent.change(screen.getByLabelText("KiCad library reference"), {
      target: { value: "Resistor_SMD:R_0402" },
    });
    fireEvent.change(screen.getByLabelText("Footprint filters"), {
      target: { value: "R_*, *_0402_* , " },
    });
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "10k" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(put).toHaveBeenCalled());
    const [path, payload] = put.mock.calls[0];
    expect(path).toBe(`/parts/${PART_ID}/eda`);
    expect(payload).toMatchObject({
      symbol_id: SYMBOL_ID,
      symbol_ref_external: null,
      footprint_id: null,
      footprint_ref_external: "Resistor_SMD:R_0402",
      // Blank entries dropped, surrounding whitespace trimmed.
      footprint_filters: ["R_*", "*_0402_*"],
      value: "10k",
    });
  });

  it("sends nulls for a slot switched back to the category default", async () => {
    mockReads({
      part_id: PART_ID,
      symbol_id: SYMBOL_ID,
      symbol_ref_external: null,
      footprint_id: null,
      footprint_ref_external: null,
      spice_datafile_id: null,
      value: null,
      keywords: null,
      footprint_filters: null,
      exclude_from_bom: false,
      exclude_from_board: false,
      exclude_from_sim: true,
      sim_device: null,
      sim_pins: null,
      sim_params: null,
    });
    const put = vi.spyOn(api, "put").mockResolvedValue({});
    renderPartCad();

    // PUT replaces rather than merges, so clearing a slot is expressible
    // — this is the case a merge-shaped API could not represent.
    fireEvent.click((await screen.findAllByLabelText("None (category default)"))[0]);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put.mock.calls[0][1]).toMatchObject({
      symbol_id: null,
      symbol_ref_external: null,
    });
  });

  it("uploads a symbol and selects the row it created", async () => {
    mockReads();
    const upload = vi.spyOn(api, "upload").mockResolvedValue({ id: SYMBOL_ID });
    renderPartCad();

    fireEvent.click((await screen.findAllByLabelText("Hosted here"))[0]);
    const input = screen.getByLabelText("Upload symbol") as HTMLInputElement;
    const file = new File(['(symbol "R")'], "R.kicad_sym", { type: "application/octet-stream" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(upload).toHaveBeenCalled());
    const [path, form] = upload.mock.calls[0];
    expect(path).toBe("/eda/symbols");
    expect((form as FormData).get("file")).toBe(file);

    // The freshly uploaded row becomes the selection, so the user doesn't
    // have to find it in the list they just added to.
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Symbol from this workspace") as HTMLSelectElement).value,
      ).toBe(SYMBOL_ID),
    );
  });

  it("offers 3D models only once a hosted footprint is chosen", async () => {
    mockReads();
    renderPartCad();

    fireEvent.click((await screen.findAllByLabelText("Hosted here"))[1]);
    fireEvent.change(screen.getByLabelText("Footprint from this workspace"), {
      target: { value: FOOTPRINT_ID },
    });

    const select = (await screen.findByLabelText("Attach a model")) as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.textContent);
    // The SPICE model is absent — only STEP and WRL attach to a footprint,
    // and the server rejects anything else.
    expect(options).toContain("R_0402.step");
    expect(options).not.toContain("resistor.lib");
  });
});
