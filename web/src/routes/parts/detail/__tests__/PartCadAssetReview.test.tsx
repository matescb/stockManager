// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { EdaAssetEntry } from "@/lib/schemas";
import { PartCadAssetReview } from "../PartCadAssetReview";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const PART_ID = "11111111-1111-4111-8111-111111111111";

function entry(overrides: Partial<EdaAssetEntry> = {}): EdaAssetEntry {
  return {
    id: "22222222-2222-4222-8222-222222222222",
    name: "OPA192.lib",
    sha256: "d".repeat(64),
    source: "manual",
    origin: "https://example.com/opa192.zip",
    origin_from: "metadata",
    model_kind: "vendor",
    simulators: ["ngspice-43"],
    valid_for: ["dc", "ac"],
    not_valid_for: ["noise"],
    note: null,
    review: { state: "unreviewed", by: null, by_name: null, at: null, against: null },
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PartCadAssetReview partId={PART_ID} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PartCadAssetReview", () => {
  it("shows state and metadata, and no controls without can_review", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue({
      assets: {
        spice: entry(),
        symbol: entry({
          name: "OPA192",
          origin: null,
          model_kind: null,
          simulators: [],
          valid_for: [],
          not_valid_for: [],
          review: {
            state: "reviewed",
            by: "u1",
            by_name: "Owner",
            at: "2026-11-20T10:00:00Z",
            against: "datasheet rev C",
          },
        }),
      },
      can_review: false,
    });
    renderPanel();

    const spice = await screen.findByTestId("asset-review-spice");
    expect(spice.textContent).toContain("unreviewed");
    expect(spice.textContent).toContain("valid for dc, ac");
    expect(spice.textContent).toContain("not for noise");
    const symbol = screen.getByTestId("asset-review-symbol");
    expect(symbol.textContent).toContain("reviewed");
    expect(symbol.textContent).toContain("checked against datasheet rev C");
    expect(screen.queryByRole("button", { name: "Mark reviewed" })).toBeNull();
  });

  it("lets an owner mark a file reviewed against the shown sha256", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue({
      assets: { spice: entry() },
      can_review: true,
    });
    const put = vi.spyOn(api, "put").mockResolvedValue({});
    renderPanel();

    const input = await screen.findByLabelText("SPICE model checked against");
    fireEvent.change(input, { target: { value: "TI app note fig 3" } });
    fireEvent.click(screen.getByRole("button", { name: "Mark reviewed" }));

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(`/parts/${PART_ID}/eda/spice/review`, {
        state: "reviewed",
        sha256: "d".repeat(64),
        against: "TI app note fig 3",
      }),
    );
  });

  it("renders nothing when the part has no hosted files", async () => {
    const get = vi
      .spyOn(api.parsed, "get")
      .mockResolvedValue({ assets: {}, can_review: true });
    renderPanel();
    await waitFor(() => expect(get).toHaveBeenCalled());
    // Loading renders the card shell; success with no files removes it.
    await waitFor(() => expect(screen.queryByText("File review")).toBeNull());
  });
});
