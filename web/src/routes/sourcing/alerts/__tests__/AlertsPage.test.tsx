// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";
import { api } from "@/lib/api";
import AlertsPage from "../AlertsPage";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const partId = "11111111-1111-4111-8111-111111111111";
const projectId = "22222222-2222-4222-8222-222222222222";

function alert(overrides: Record<string, unknown> = {}) {
  return {
    id: "alert-1",
    workspace_id: "ws-1",
    alert_type: "stock_below",
    part_id: partId,
    project_id: null,
    threshold: { qty: 10 },
    country_code: null,
    currency_code: null,
    distributor_filter: null,
    notify_user_ids: null,
    cooldown_seconds: 86400,
    enabled: true,
    last_checked_at: "2026-05-10T12:00:00+00:00",
    last_notified_at: null,
    archived_at: null,
    created_by: null,
    created_at: "2026-05-10T10:00:00+00:00",
    updated_at: "2026-05-10T10:00:00+00:00",
    ...overrides,
  };
}

function parts() {
  return [{
    id: partId,
    part_type: "linked",
    name: "STM32",
    manufacturer: "ST",
    mpn: "STM32F103C8T6",
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
    linked_provider: null,
    linked_external_id: null,
    last_refresh_at: null,
    description_locally_edited: false,
    archived_at: null,
    on_hand: 0,
    reserved: 0,
    available: 0,
    image_url: null,
  }];
}

function projects() {
  return [{
    id: projectId,
    name: "Amplifier",
    description: null,
    notes_markdown: null,
    associated_subassembly_part_id: null,
    archived_at: null,
    created_at: "2026-05-10T12:00:00+00:00",
    updated_at: "2026-05-10T12:00:00+00:00",
  }];
}

function mockReads() {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (String(path).startsWith("/parts?")) return parts() as never;
    if (String(path).startsWith("/projects?")) return projects() as never;
    if (String(path).startsWith("/sourcing/alerts")) {
      if (String(path).includes("alert_type=bom_buyable")) {
        return [alert({
          id: "alert-2",
          alert_type: "bom_buyable",
          part_id: null,
          project_id: projectId,
          threshold: { build_quantity: 5 },
        })] as never;
      }
      return [
        alert(),
        alert({
          id: "alert-2",
          alert_type: "bom_buyable",
          part_id: null,
          project_id: projectId,
          threshold: { build_quantity: 5 },
        }),
      ] as never;
    }
    throw new Error(`unexpected GET ${path}`);
  });
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ConfirmDialogProvider>
        <AlertsPage />
      </ConfirmDialogProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("AlertsPage", () => {
  it("renders list of alerts from server", async () => {
    mockReads();

    renderPage();

    expect(await screen.findByText("STM32")).toBeDefined();
    expect(screen.getAllByText("Stock below").length).toBeGreaterThan(0);
    expect(screen.getAllByText("BOM buyable").length).toBeGreaterThan(0);
    expect(screen.getByText("Amplifier")).toBeDefined();
    expect(screen.getByText("Below 10")).toBeDefined();
  });

  it("filter by alert_type narrows visible rows", async () => {
    const user = userEvent.setup();
    mockReads();

    renderPage();

    await screen.findByText("STM32");
    await user.selectOptions(screen.getByLabelText("Alert type"), "bom_buyable");

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith("/sourcing/alerts?alert_type=bom_buyable", expect.any(Object));
    });
    const table = screen.getByRole("table");
    expect(within(table).getByText("BOM buyable")).toBeDefined();
    expect(within(table).queryByText("Stock below")).toBeNull();
  });

  it("archive prompts confirmation then DELETEs", async () => {
    const user = userEvent.setup();
    mockReads();
    const del = vi.spyOn(api, "delete").mockResolvedValue(alert({ archived_at: "2026-05-10T13:00:00+00:00" }) as never);

    renderPage();

    await screen.findByText("STM32");
    await user.click(screen.getAllByRole("button", { name: "Archive" })[0]);
    const archiveButtons = await screen.findAllByRole("button", { name: "Archive" });
    await user.click(archiveButtons[archiveButtons.length - 1]);

    await waitFor(() => {
      expect(del).toHaveBeenCalledWith("/sourcing/alerts/alert-1");
    });
  });
});
