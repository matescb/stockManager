/**
 * DOM tests for batch label printing.
 *
 * There is no server-side batch endpoint here — each selected object is one
 * `test-print` call — so the two behaviours that only exist client-side are
 * the ones worth pinning:
 *  - the run STOPS at the first failure and says how far it got, rather than
 *    queuing a failed print job per selected row (which is exactly what would
 *    happen today, with `PRINT_HOST` unset in prod);
 *  - the selection is capped at the server's 20/minute rate limit instead of
 *    firing straight into a 429.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import BatchPrintDialog, { MAX_BATCH, type BatchPrintItem } from "../BatchPrintDialog";

const parsedGet = vi.fn();
const parsedPost = vi.fn();
const toastError = vi.fn();
const toastSuccess = vi.fn();

vi.mock("sonner", () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a),
    success: (...a: unknown[]) => toastSuccess(...a),
  },
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ workspaceId: "ws-1" }) }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      parsed: {
        ...actual.api.parsed,
        get: (...args: unknown[]) => parsedGet(...args),
        post: (...args: unknown[]) => parsedPost(...args),
      },
    },
  };
});

const TEMPLATE = {
  id: "tpl-1",
  name: "Bin label",
  entity_type: "storage_location",
  width_mm: 50,
  height_mm: 30,
  gap_mm: 3,
  heat: 100,
  speed: 0,
  method: "T",
  dpi: 300,
  is_default: true,
  elements: [],
};

function items(count: number): BatchPrintItem[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `bin-${i}`,
    label: `Bin ${i}`,
  }));
}

async function open(count: number) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onDone = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <BatchPrintDialog
        open
        entityType="storage_location"
        items={items(count)}
        onClose={() => {}}
        onDone={onDone}
      />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /^Print \d+ label/ })).toHaveProperty(
      "disabled",
      false,
    ),
  );
  return { onDone };
}

beforeEach(() => {
  cleanup();
  parsedGet.mockReset();
  parsedPost.mockReset();
  toastError.mockReset();
  toastSuccess.mockReset();
  parsedGet.mockResolvedValue([TEMPLATE]);
});

describe("BatchPrintDialog", () => {
  it("prints one label per selected object", async () => {
    parsedPost.mockResolvedValue({ print_job_id: "j", status: "printed", code: "C" });
    const { onDone } = await open(3);
    fireEvent.click(screen.getByRole("button", { name: /^Print 3 labels/ }));

    await waitFor(() => expect(parsedPost).toHaveBeenCalledTimes(3));
    expect(parsedPost.mock.calls.map((c) => (c[2] as { entity_id: string }).entity_id)).toEqual([
      "bin-0",
      "bin-1",
      "bin-2",
    ]);
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("stops at the first printer failure and reports how far it got", async () => {
    parsedPost
      .mockResolvedValueOnce({ print_job_id: "j1", status: "printed", code: "C" })
      .mockRejectedValue(
        new ApiError(
          409,
          {
            data: null,
            status: { category: "conflict", message: "unreachable" },
            code: "printer.unreachable",
            print_job_id: "job-9",
          } as never,
          "unreachable",
        ),
      );
    const { onDone } = await open(4);
    fireEvent.click(screen.getByRole("button", { name: /^Print 4 labels/ }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Stopped after 1 of 4 labels");
    expect(alert.textContent).toContain("Printer not configured or unreachable");
    // Two calls: the one that worked and the one that failed. Not four.
    expect(parsedPost).toHaveBeenCalledTimes(2);
    expect(onDone).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("caps the batch at the server's per-minute print limit", async () => {
    await open(MAX_BATCH + 5);
    expect(
      screen.getByText(new RegExp(`only the\\s+first ${MAX_BATCH} of your selection`)),
    ).toBeDefined();
    expect(
      screen.getByRole("button", { name: new RegExp(`^Print ${MAX_BATCH} labels`) }),
    ).toBeDefined();
  });
});
