/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { ProvidersCard, type ProvidersWorkspace } from "../ProvidersCard";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

/** DigiKey is the primary, so only Mouser is offered as a secondary. */
const workspace: ProvidersWorkspace = {
  parts_provider: "digikey",
  provider_credentials: [
    { provider: "digikey", has_api_key: true, has_api_secret: true },
  ],
};

function renderCard(current: ProvidersWorkspace = workspace) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <ProvidersCard workspace={current} workspaceId="ws-1" />
    </QueryClientProvider>,
  );
  return { invalidateSpy };
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("ProvidersCard", () => {
  it("offers every known provider except the primary", () => {
    renderCard();

    expect(screen.getByText("Mouser")).toBeDefined();
    expect(screen.queryByText("DigiKey")).toBeNull();
  });

  it("saves a secondary key through the provider-credentials endpoint", async () => {
    const put = vi.spyOn(api, "put").mockResolvedValue({
      provider: "mouser",
      has_api_key: true,
      has_api_secret: false,
      provider_credentials: [
        { provider: "mouser", has_api_key: true, has_api_secret: false },
      ],
    });
    const user = userEvent.setup();
    const { invalidateSpy } = renderCard();

    await user.type(screen.getByLabelText("Mouser API key"), "new-key");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(put).toHaveBeenCalledWith("/workspaces/current/provider-credentials", {
        provider: "mouser",
        api_key: "new-key",
      });
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["ws", "ws-1", "ws", "current"],
    });
    expect(toast.success).toHaveBeenCalledWith("Mouser credentials saved.");
  });

  it("shows a configured pill and a Clear button once a key is stored", async () => {
    const put = vi.spyOn(api, "put").mockResolvedValue({
      provider: "mouser",
      has_api_key: false,
      has_api_secret: false,
      provider_credentials: [],
    });
    const user = userEvent.setup();
    renderCard({
      parts_provider: "digikey",
      provider_credentials: [
        { provider: "mouser", has_api_key: true, has_api_secret: false },
      ],
    });

    expect(screen.getByLabelText("Mouser credentials configured")).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Clear" }));

    await waitFor(() => {
      expect(put).toHaveBeenCalledWith("/workspaces/current/provider-credentials", {
        provider: "mouser",
        api_key: "",
        api_secret: "",
      });
    });
    expect(toast.success).toHaveBeenCalledWith("Mouser credentials cleared.");
  });

  it("asks for both credentials when the secondary is DigiKey", () => {
    renderCard({ parts_provider: "mouser", provider_credentials: [] });

    expect(screen.getByLabelText("DigiKey Client ID")).toBeDefined();
    expect(screen.getByLabelText("DigiKey Client Secret")).toBeDefined();
    // Save stays disabled until the operator actually types something.
    expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(
      true,
    );
  });
});
