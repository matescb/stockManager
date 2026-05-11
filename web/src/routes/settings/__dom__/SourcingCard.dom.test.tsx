import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { SourcingCard, type SourcingWorkspace } from "../SourcingCard";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const baseWorkspace: SourcingWorkspace = {
  id: "ws-1",
  sourcing_provider: "none",
  sourcing_country_code: null,
  sourcing_currency_code: null,
  sourcing_language_code: null,
  sourcing_preferred_distributors: null,
  active_countries: ["CZ", "DE", "US"],
  active_currencies: ["EUR", "USD"],
  sourcing_use_cached_for_dashboards: true,
  has_sourcing_company_id: false,
  has_sourcing_api_key: false,
};

function renderCard(workspace: SourcingWorkspace = baseWorkspace) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <SourcingCard workspace={workspace} workspaceId={workspace.id} />
    </QueryClientProvider>,
  );
  return { client, invalidateSpy };
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("SourcingCard", () => {
  it("renders empty form for unconfigured workspace", () => {
    renderCard();

    expect(screen.getByRole("heading", { name: "Sourcing provider" })).toBeDefined();
    expect(screen.getByLabelText("Provider")).toBeDefined();
    expect(screen.getByLabelText("CompanyId (deprecated)")).toBeDefined();
    expect(screen.getByLabelText("API Key")).toBeDefined();
    expect(screen.getByLabelText("Country")).toBeDefined();
    expect(screen.getByLabelText("Currency")).toBeDefined();
    expect(screen.getByLabelText("Language")).toBeDefined();
    expect(screen.getByRole("option", { name: "Default (en)" })).toBeDefined();
    expect(screen.getByRole("option", { name: "Chinese, Traditional (zh-hant)" })).toBeDefined();
    expect(screen.getByLabelText("Preferred distributors")).toBeDefined();
    expect(screen.getByLabelText("Use cached data for dashboards")).toBeDefined();
    expect(screen.queryByText("Configured ✓")).toBeNull();
  });

  it("renders country select with active countries", () => {
    renderCard();

    expect(screen.getByRole("combobox", { name: "Country" })).toBeDefined();
    expect(screen.getByRole("option", { name: "CZ" })).toBeDefined();
    expect(screen.getByRole("option", { name: "DE" })).toBeDefined();
    expect(screen.queryByRole("textbox", { name: "Country" })).toBeNull();
  });

  it("disables save when country is empty", () => {
    renderCard({ ...baseWorkspace, sourcing_currency_code: "EUR" });

    expect(screen.getByRole("button", { name: "Save" })).toHaveProperty("disabled", true);
  });

  it("submits PATCH with the expected body shape on Save", async () => {
    const user = userEvent.setup();
    const patchSpy = vi.spyOn(api, "patch").mockResolvedValue({
      ...baseWorkspace,
      sourcing_provider: "trustedparts",
      sourcing_country_code: "CZ",
      sourcing_currency_code: "EUR",
      sourcing_language_code: "de",
      sourcing_preferred_distributors: ["DigiKey", "Mouser"],
      active_countries: ["CZ", "DE", "US"],
      active_currencies: ["EUR", "USD"],
      sourcing_use_cached_for_dashboards: false,
      has_sourcing_company_id: true,
      has_sourcing_api_key: true,
    });
    const { invalidateSpy } = renderCard();

    await user.selectOptions(screen.getByLabelText("Provider"), "trustedparts");
    await user.type(screen.getByLabelText("CompanyId (deprecated)"), "company-123");
    await user.type(screen.getByLabelText("API Key"), "api-456");
    await user.selectOptions(screen.getByLabelText("Country"), "CZ");
    await user.selectOptions(screen.getByLabelText("Currency"), "EUR");
    await user.selectOptions(screen.getByLabelText("Language"), "de");
    await user.type(screen.getByLabelText("Preferred distributors"), "DigiKey, Mouser");
    await user.click(screen.getByLabelText("Use cached data for dashboards"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchSpy).toHaveBeenCalledWith("/workspaces/current", {
        sourcing_provider: "trustedparts",
        sourcing_country_code: "CZ",
        sourcing_currency_code: "EUR",
        sourcing_language_code: "de",
        sourcing_preferred_distributors: ["DigiKey", "Mouser"],
        sourcing_use_cached_for_dashboards: false,
        sourcing_company_id: "company-123",
        sourcing_api_key: "api-456",
      });
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ws", "ws-1", "ws", "current"] });
    expect(screen.getByText("Configured ✓")).toBeDefined();
  });

  it("submits with selected country code", async () => {
    const user = userEvent.setup();
    const patchSpy = vi.spyOn(api, "patch").mockResolvedValue({
      ...baseWorkspace,
      sourcing_country_code: "US",
      sourcing_currency_code: "USD",
    });
    renderCard();

    await user.selectOptions(screen.getByLabelText("Country"), "US");
    await user.selectOptions(screen.getByLabelText("Currency"), "USD");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchSpy).toHaveBeenCalledWith(
        "/workspaces/current",
        expect.objectContaining({
          sourcing_country_code: "US",
          sourcing_currency_code: "USD",
        }),
      );
    });
  });

  it("maps the default language option to null on Save", async () => {
    const user = userEvent.setup();
    const patchSpy = vi.spyOn(api, "patch").mockResolvedValue(baseWorkspace);
    renderCard({
      ...baseWorkspace,
      sourcing_country_code: "CZ",
      sourcing_currency_code: "EUR",
      sourcing_language_code: "fr",
    });

    await user.selectOptions(screen.getByLabelText("Language"), "");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchSpy).toHaveBeenCalledWith(
        "/workspaces/current",
        expect.objectContaining({ sourcing_language_code: null }),
      );
    });
  });

  it("surfaces test-connection result on success", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "post").mockResolvedValue({ ok: true, message: "OK", latency_ms: 12 });
    renderCard();

    await user.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("OK (12 ms)");
    });
    expect(toast.success).toHaveBeenCalledWith("Sourcing connection OK: OK (12 ms)");
  });

  it("surfaces test-connection failure message on auth error", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "post").mockResolvedValue({
      ok: false,
      message: "invalid credentials",
      latency_ms: 9,
    });
    renderCard();

    await user.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain("invalid credentials (9 ms)");
    });
    expect(toast.error).toHaveBeenCalledWith(
      "Sourcing connection failed: invalid credentials (9 ms)",
    );
  });

  it("does not display credential values, even when typed and saved", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "patch").mockResolvedValue({
      ...baseWorkspace,
      sourcing_provider: "trustedparts",
      has_sourcing_company_id: true,
      has_sourcing_api_key: true,
    });
    renderCard();

    await user.type(screen.getByLabelText("CompanyId (deprecated)"), "secret-company-id");
    await user.type(screen.getByLabelText("API Key"), "secret-api-key");
    await user.selectOptions(screen.getByLabelText("Country"), "CZ");
    await user.selectOptions(screen.getByLabelText("Currency"), "EUR");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(screen.getByText("Configured ✓")).toBeDefined();
    });
    expect(screen.queryByDisplayValue("secret-company-id")).toBeNull();
    expect(screen.queryByDisplayValue("secret-api-key")).toBeNull();
    expect(screen.queryByText("secret-company-id")).toBeNull();
    expect(screen.queryByText("secret-api-key")).toBeNull();
  });
});
