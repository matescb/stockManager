// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { PartCadPinPad } from "../PartCadPinPad";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ workspaceId: "ws-1" }) }));

const PART_ID = "11111111-1111-4111-8111-111111111111";

function check(overrides: Record<string, unknown> = {}) {
  return {
    verdict: "fail",
    symbol_sha256: "a".repeat(64),
    footprint_sha256: "b".repeat(64),
    findings: {
      classified: [
        { kind: "pad_without_pin", number: "33", class: "exposed_pad", reason: "pad is 9x" },
        { kind: "pad_without_pin", number: "9", class: "electrical", reason: "no pin" },
        {
          kind: "pad_without_pin",
          number: "5",
          class: "nc_pin",
          reason: "likely NC",
          accepted: { by: "u1", by_name: "Ann", at: "2026-11-24T10:00:00+00:00", note: "NC" },
        },
      ],
      pads_without_pin: ["5", "9", "33"],
    },
    electrical_count: 1,
    warnings_count: 1,
    accepted_count: 1,
    stale_acceptances: [
      {
        finding_kind: "pad_without_pin",
        number: "17",
        class: "exposed_pad",
        symbol_sha256: "c".repeat(64),
        footprint_sha256: "d".repeat(64),
        by: "u1",
        by_name: "Ann",
        at: "2026-11-20T10:00:00+00:00",
        note: null,
      },
    ],
    can_accept: true,
    ...overrides,
  };
}

function renderIt() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PartCadPinPad partId={PART_ID} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PartCadPinPad", () => {
  it("lists findings with their class, acceptances and stale acceptances", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(check());
    renderIt();

    expect(await screen.findByText("Mismatch")).toBeTruthy();
    const ep = screen.getByTestId("pin-pad-pad_without_pin:33");
    expect(within(ep).getByText("exposed pad")).toBeTruthy();
    const nc = screen.getByTestId("pin-pad-pad_without_pin:5");
    expect(within(nc).getByText(/Accepted by Ann/)).toBeTruthy();
    expect(within(nc).getByRole("button", { name: "Revoke" })).toBeTruthy();
    expect(screen.getByText(/the files have changed since/)).toBeTruthy();
  });

  it("requires a note before accepting an electrical finding", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(check());
    const post = vi.spyOn(api, "post").mockResolvedValue({});
    renderIt();

    const row = await screen.findByTestId("pin-pad-pad_without_pin:9");
    const button = within(row).getByRole("button", { name: "Accept" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(within(row).getByLabelText("Note for Pad without pin 9"), {
      target: { value: "test point" },
    });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(`/parts/${PART_ID}/eda/pin-pad/accept`, {
        finding_kind: "pad_without_pin",
        number: "9",
        note: "test point",
      }),
    );
  });

  it("accepts a warning without a note and revokes with a DELETE", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(check());
    const post = vi.spyOn(api, "post").mockResolvedValue({});
    const del = vi.spyOn(api, "delete").mockResolvedValue({});
    renderIt();

    const ep = await screen.findByTestId("pin-pad-pad_without_pin:33");
    fireEvent.click(within(ep).getByRole("button", { name: "Accept" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(`/parts/${PART_ID}/eda/pin-pad/accept`, {
        finding_kind: "pad_without_pin",
        number: "33",
        note: null,
      }),
    );
    const nc = screen.getByTestId("pin-pad-pad_without_pin:5");
    fireEvent.click(within(nc).getByRole("button", { name: "Revoke" }));
    await waitFor(() =>
      expect(del).toHaveBeenCalledWith(
        `/parts/${PART_ID}/eda/pin-pad/accept?finding_kind=pad_without_pin&number=5`,
      ),
    );
  });

  it("hides the controls from callers who may not accept", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(check({ can_accept: false }));
    renderIt();

    await screen.findByText("Mismatch");
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  });

  it("says not-checkable is not a pass", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(
      check({
        verdict: "not-checkable",
        reason: "symbol_external",
        detail: "the symbol is in the user's local KiCad library",
        findings: { classified: [] },
        stale_acceptances: [],
      }),
    );
    renderIt();

    expect(await screen.findByText("Not checkable")).toBeTruthy();
    expect(screen.getByText(/This is not a pass/)).toBeTruthy();
  });
});
