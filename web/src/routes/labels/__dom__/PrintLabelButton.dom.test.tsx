/**
 * DOM tests for the "Print label" action.
 *
 * The centre of gravity here is the FAILURE path, because that is the path
 * production is actually on: `PRINT_HOST` is empty in prod, so
 * `print_service.send_jscript` raises `PrinterUnreachable`, the route marks
 * the `print_jobs` row failed and RETURNS 409 `printer.unreachable` with a
 * `print_job_id` on the body (it returns rather than raises so the failed job
 * survives the transaction — see the `label_templates.py` module docstring).
 *
 * What these tests pin:
 *  - a 409 renders an intelligible message naming the recorded job, and does
 *    NOT crash or read as success;
 *  - the dialog stays open on failure so the operator can copy the job id;
 *  - a 403 (member trying to print — the endpoint is admin+) is not shown as
 *    a printer fault;
 *  - the success path posts `entity_id` + `copies` to the chosen template.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import PrintLabelButton from "../PrintLabelButton";

const parsedGet = vi.fn();
const parsedPost = vi.fn();
const toastError = vi.fn();
const toastSuccess = vi.fn();

vi.mock("sonner", () => ({
  toast: { error: (...a: unknown[]) => toastError(...a), success: (...a: unknown[]) => toastSuccess(...a) },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

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
  name: "Part label (default)",
  entity_type: "part",
  width_mm: 50,
  height_mm: 30,
  gap_mm: 3,
  heat: 100,
  speed: 0,
  method: "T",
  dpi: 300,
  is_default: true,
  elements: [{ kind: "qr", x_mm: 2, y_mm: 2, dotsize_mm: 0.5, ec: "M" }],
};

function printerUnreachable() {
  return new ApiError(
    409,
    {
      data: null,
      status: { category: "conflict", message: "the label printer is unreachable" },
      code: "printer.unreachable",
      print_job_id: "job-42",
    } as never,
    "the label printer is unreachable",
  );
}

function renderButton() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PrintLabelButton entityType="part" entityId="part-9" entityName="Widget" />
    </QueryClientProvider>,
  );
}

/** Open the dialog and wait until the template list has landed (Print enabled). */
async function openDialog() {
  renderButton();
  fireEvent.click(screen.getByRole("button", { name: "Print label" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Print" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
}

beforeEach(() => {
  cleanup();
  parsedGet.mockReset();
  parsedPost.mockReset();
  toastError.mockReset();
  toastSuccess.mockReset();
  parsedGet.mockResolvedValue([TEMPLATE]);
});

describe("PrintLabelButton", () => {
  it("fetches templates only once the dialog is opened", async () => {
    renderButton();
    expect(parsedGet).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Print label" }));
    await waitFor(() => expect(parsedGet).toHaveBeenCalled());
    expect(parsedGet.mock.calls[0][0]).toBe("/label-templates?entity_type=part");
  });

  it("posts the entity id and copies to the default template", async () => {
    parsedPost.mockResolvedValue({ print_job_id: "job-1", status: "printed", code: "ABCD1234" });
    await openDialog();
    fireEvent.click(screen.getByRole("button", { name: "Print" }));

    await waitFor(() => expect(parsedPost).toHaveBeenCalled());
    expect(parsedPost.mock.calls[0][0]).toBe("/label-templates/tpl-1/test-print");
    expect(parsedPost.mock.calls[0][2]).toEqual({ entity_id: "part-9", copies: 1 });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it("surfaces a printer-unreachable 409 as a recorded-but-not-printed message", async () => {
    parsedPost.mockRejectedValue(printerUnreachable());
    await openDialog();
    fireEvent.click(screen.getByRole("button", { name: "Print" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Printer not configured or unreachable");
    expect(alert.textContent).toContain("nothing was printed");
    expect(alert.textContent).toContain("job-42");
    expect(toastError).toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("keeps the dialog open after a printer failure so the job id stays readable", async () => {
    parsedPost.mockRejectedValue(printerUnreachable());
    await openDialog();
    fireEvent.click(screen.getByRole("button", { name: "Print" }));

    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Print" })).toBeDefined();
    expect(screen.getByRole("combobox", { name: /template/i })).toBeDefined();
  });

  it("does not blame the printer for a 403 from a non-admin", async () => {
    parsedPost.mockRejectedValue(
      new ApiError(
        403,
        { data: null, status: { category: "forbidden", message: "admin required" } },
        "admin required",
      ),
    );
    await openDialog();
    fireEvent.click(screen.getByRole("button", { name: "Print" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("admin role");
    expect(alert.textContent).not.toContain("Printer not configured");
  });

  it("explains the rate limit on a 429 rather than showing a raw error", async () => {
    parsedPost.mockRejectedValue(
      new ApiError(
        429,
        { data: null, status: { category: "rate_limited", message: "slow down" } },
        "slow down",
      ),
    );
    await openDialog();
    fireEvent.click(screen.getByRole("button", { name: "Print" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Too many print requests");
  });

  it("tells the operator when the entity type has no template yet", async () => {
    parsedGet.mockResolvedValue([]);
    renderButton();
    fireEvent.click(screen.getByRole("button", { name: "Print label" }));

    await waitFor(() =>
      expect(screen.getByText(/No label template exists for this type yet/)).toBeDefined(),
    );
    // Nothing to print, so the action is not offered as available.
    expect(screen.getByRole("button", { name: "Print" })).toHaveProperty("disabled", true);
  });
});
