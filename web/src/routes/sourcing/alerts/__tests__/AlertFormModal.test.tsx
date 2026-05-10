// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import AlertFormModal from "../AlertFormModal";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const part = {
  id: "11111111-1111-4111-8111-111111111111",
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
};

const project = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "Amplifier",
  description: null,
  notes_markdown: null,
  associated_subassembly_part_id: null,
  archived_at: null,
  created_at: "2026-05-10T12:00:00+00:00",
  updated_at: "2026-05-10T12:00:00+00:00",
};

function workspace() {
  return {
    sourcing_country_code: "US",
    sourcing_currency_code: "USD",
    sourcing_preferred_distributors: ["DigiKey"],
    active_countries: ["US", "CZ"],
    active_currencies: ["USD", "EUR"],
    active_distributors: ["DigiKey", "Mouser"],
  };
}

function members() {
  return [
    {
      id: "member-1",
      user_id: "33333333-3333-4333-8333-333333333333",
      email: "admin@example.com",
      name: "Admin User",
      role: "admin",
      status: "active",
    },
    {
      id: "member-2",
      user_id: "44444444-4444-4444-8444-444444444444",
      email: "disabled@example.com",
      name: "Disabled User",
      role: "member",
      status: "disabled",
    },
  ];
}

function mockApiReads() {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/workspaces/current") return workspace() as never;
    if (path === "/workspaces/members") return members() as never;
    if (String(path).startsWith("/parts?")) return [part] as never;
    if (String(path).startsWith("/projects?")) return [project] as never;
    throw new Error(`unexpected GET ${path}`);
  });
}

function renderModal(
  props: Partial<ComponentProps<typeof AlertFormModal>> = {},
) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AlertFormModal open onClose={vi.fn()} {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("AlertFormModal", () => {
  it("bom_buyable shows project picker and build_quantity field", async () => {
    mockApiReads();

    renderModal({
      initialValues: { alert_type: "bom_buyable", project_id: project.id, build_quantity: 7 },
      allowedTypes: ["bom_buyable"],
    });

    expect(await screen.findByLabelText("Project")).toBeDefined();
    expect(screen.getByLabelText("Build quantity")).toBeDefined();
    expect((screen.getByLabelText("Build quantity") as HTMLInputElement).value).toBe("7");
    expect(screen.queryByLabelText("Part")).toBeNull();
  });

  it("back_in_stock shows no threshold field", async () => {
    mockApiReads();

    renderModal({
      initialValues: { alert_type: "back_in_stock", part_id: part.id },
      allowedTypes: ["back_in_stock"],
    });

    expect(await screen.findByText("This alert triggers on an availability transition; no numeric threshold is needed.")).toBeDefined();
    expect(screen.queryByLabelText("Quantity")).toBeNull();
    expect(screen.queryByLabelText("Delta percent")).toBeNull();
    expect(screen.queryByLabelText("Build quantity")).toBeNull();
  });

  it("lifecycle_risk_changed shows must_contain and case_sensitive fields", async () => {
    mockApiReads();

    renderModal({
      initialValues: { alert_type: "lifecycle_risk_changed", part_id: part.id },
      allowedTypes: ["lifecycle_risk_changed"],
    });

    expect(await screen.findByLabelText("Must contain")).toBeDefined();
    expect(screen.getByLabelText("Case sensitive")).toBeDefined();
    expect(screen.queryByLabelText("Quantity")).toBeNull();
    expect(screen.queryByLabelText("Delta percent")).toBeNull();
  });

  it("tariff_status_changed shows no threshold fields", async () => {
    mockApiReads();

    renderModal({
      initialValues: { alert_type: "tariff_status_changed", part_id: part.id },
      allowedTypes: ["tariff_status_changed"],
    });

    expect(await screen.findByText("This alert triggers on any tariff status transition; no threshold is needed.")).toBeDefined();
    expect(screen.queryByLabelText("Must contain")).toBeNull();
    expect(screen.queryByLabelText("Case sensitive")).toBeNull();
    expect(screen.queryByLabelText("Quantity")).toBeNull();
    expect(screen.queryByLabelText("Delta percent")).toBeNull();
    expect(screen.queryByLabelText("Build quantity")).toBeNull();
  });

  it("cooldown < 60 surfaces validation error", async () => {
    const user = userEvent.setup();
    mockApiReads();
    vi.spyOn(api, "post").mockResolvedValue({} as never);

    renderModal({
      initialValues: { alert_type: "bom_buyable", project_id: project.id, build_quantity: 1 },
      allowedTypes: ["bom_buyable"],
    });

    await screen.findByLabelText("Project");
    await user.clear(screen.getByLabelText("Cooldown seconds"));
    await user.type(screen.getByLabelText("Cooldown seconds"), "59");
    await user.click(screen.getByRole("button", { name: "Create alert" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Cooldown must be at least 60 seconds.");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("submit posts the correct payload shape per alert type", async () => {
    const user = userEvent.setup();
    mockApiReads();
    const post = vi.spyOn(api, "post").mockResolvedValue({ id: "alert-1" } as never);

    renderModal({
      initialValues: { alert_type: "price_changed", part_id: part.id },
      allowedTypes: ["price_changed"],
    });

    await screen.findByText("STM32");
    await user.clear(screen.getByLabelText("Delta percent"));
    await user.type(screen.getByLabelText("Delta percent"), "12.5");
    await user.selectOptions(screen.getByLabelText("Country"), "CZ");
    await user.selectOptions(screen.getByLabelText("Currency"), "EUR");
    await user.selectOptions(screen.getByLabelText("Distributor filter"), ["Mouser"]);
    await user.selectOptions(screen.getByLabelText("Recipients"), ["33333333-3333-4333-8333-333333333333"]);
    await user.click(screen.getByRole("button", { name: "Create alert" }));

    await waitFor(() => {
      expect(post).toHaveBeenCalledWith("/sourcing/alerts", {
        alert_type: "price_changed",
        part_id: part.id,
        project_id: null,
        threshold: { delta_pct: 12.5 },
        cooldown_seconds: 86400,
        enabled: true,
        notify_user_ids: ["33333333-3333-4333-8333-333333333333"],
        country_code: "CZ",
        currency_code: "EUR",
        distributor_filter: ["DigiKey", "Mouser"],
      });
    });
  });

  it("submit posts lifecycle string-change threshold", async () => {
    const user = userEvent.setup();
    mockApiReads();
    const post = vi.spyOn(api, "post").mockResolvedValue({ id: "alert-1" } as never);

    renderModal({
      initialValues: { alert_type: "lifecycle_risk_changed", part_id: part.id },
      allowedTypes: ["lifecycle_risk_changed"],
    });

    await screen.findByText("STM32");
    await user.type(screen.getByLabelText("Must contain"), "EOL");
    await user.click(screen.getByLabelText("Case sensitive"));
    await user.click(screen.getByRole("button", { name: "Create alert" }));

    await waitFor(() => {
      expect(post).toHaveBeenCalledWith("/sourcing/alerts", expect.objectContaining({
        alert_type: "lifecycle_risk_changed",
        part_id: part.id,
        project_id: null,
        threshold: { must_contain: "EOL", case_sensitive: true },
      }));
    });
  });

  it("notify_user_ids picker excludes non-members", async () => {
    mockApiReads();

    renderModal({
      initialValues: { alert_type: "back_in_stock", part_id: part.id },
      allowedTypes: ["back_in_stock"],
    });

    expect(await screen.findByRole("option", { name: "Admin User (admin@example.com)" })).toBeDefined();
    const recipients = screen.getByLabelText("Recipients");
    expect(within(recipients).queryByRole("option", { name: "Disabled User (disabled@example.com)" })).toBeNull();
  });
});
