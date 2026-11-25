// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import PartSubstitutes, { type Sub } from "../PartSubstitutes";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const PART_ID = "11111111-1111-4111-8111-111111111111";
const SUB_ID = "22222222-2222-4222-8222-222222222222";

function link(overrides: Partial<Sub> = {}): Sub {
  return {
    part_id: SUB_ID,
    direction: "bidirectional",
    relation: null,
    evaluation: "not-evaluated",
    evidence: null,
    evaluated_by: null,
    evaluated_at: null,
    ...overrides,
  };
}

function renderTab(subs: Sub[]) {
  vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    if (path.endsWith("/substitutes")) return subs as never;
    return [{ id: SUB_ID, name: "Alt op-amp", mpn: "ALT-1" }] as never;
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/parts/${PART_ID}/substitutes`]}>
        <Routes>
          <Route path="/parts/:partId/substitutes" element={<PartSubstitutes />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PartSubstitutes", () => {
  it("shows relation, evaluation and evidence for a link", async () => {
    renderTab([
      link({
        relation: "pin-compatible",
        evaluation: "evaluated-ok",
        evidence: "SIM-012",
        evaluated_at: "2026-11-24T10:00:00+00:00",
      }),
    ]);

    expect(await screen.findByText("Pin-compatible")).toBeTruthy();
    expect(screen.getByText("Evaluated OK")).toBeTruthy();
    expect(screen.getByText(/Evidence: SIM-012/)).toBeTruthy();
  });

  it("a link with no evaluation reads Not evaluated", async () => {
    renderTab([link()]);

    expect(await screen.findByText("Not evaluated")).toBeTruthy();
  });

  it("edits relation, evaluation and evidence through PATCH", async () => {
    renderTab([link()]);
    const patch = vi.spyOn(api, "patch").mockResolvedValue(link() as never);

    fireEvent.click(await screen.findByText("Edit"));
    fireEvent.change(screen.getByLabelText("Relation"), { target: { value: "functional" } });
    fireEvent.change(screen.getByLabelText("Evaluation"), { target: { value: "rejected" } });
    fireEvent.change(screen.getByLabelText("Evidence"), { target: { value: " TEST-4 " } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith(`/parts/${PART_ID}/substitutes/${SUB_ID}`, {
        relation: "functional",
        evaluation: "rejected",
        evidence: "TEST-4",
      }),
    );
  });
});
