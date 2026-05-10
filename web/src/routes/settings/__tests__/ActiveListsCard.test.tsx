/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { ActiveListsCard, type ActiveListsWorkspace } from "../ActiveListsCard";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const workspace: ActiveListsWorkspace = {
  id: "ws-1",
  active_currencies: ["EUR", "USD"],
  active_countries: ["CZ", "DE"],
  active_distributors: ["DigiKey", "Mouser"],
};

const masterLists = {
  currencies: ["EUR", "USD", "JPY"],
  countries: ["CZ", "DE", "JP"],
  distributors: ["DigiKey", "Mouser", "RS Components"],
};

function renderCard(current: ActiveListsWorkspace = workspace) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <ActiveListsCard workspace={current} workspaceId={current.id} />
    </QueryClientProvider>,
  );
  return { invalidateSpy };
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.spyOn(api, "get").mockResolvedValue(masterLists);
});

describe("ActiveListsCard", () => {
  it("renders three multi-select sections", async () => {
    renderCard();

    expect(screen.getByRole("heading", {
      name: "Active currencies / countries / distributors",
    })).toBeDefined();
    expect(screen.getByRole("group", { name: "Active currencies" })).toBeDefined();
    expect(screen.getByRole("group", { name: "Active countries" })).toBeDefined();
    expect(screen.getByRole("group", { name: "Active distributors" })).toBeDefined();
    await waitFor(() => {
      expect(screen.getByLabelText("JPY")).toBeDefined();
    });
  });

  it("searching narrows the visible items", async () => {
    const user = userEvent.setup();
    renderCard();

    const group = screen.getByRole("group", { name: "Active currencies" });
    await waitFor(() => {
      expect(within(group).getByLabelText("JPY")).toBeDefined();
    });

    await user.type(screen.getByLabelText("Search currencies"), "JP");

    expect(within(group).getByLabelText("JPY")).toBeDefined();
    expect(within(group).queryByLabelText("EUR")).toBeNull();
    expect(within(group).queryByLabelText("USD")).toBeNull();
  });

  it("save sends PATCH with the chosen lists", async () => {
    const user = userEvent.setup();
    const patchSpy = vi.spyOn(api, "patch").mockResolvedValue({
      ...workspace,
      active_currencies: ["EUR", "JPY"],
    });
    const { invalidateSpy } = renderCard();

    const group = screen.getByRole("group", { name: "Active currencies" });
    await waitFor(() => {
      expect(within(group).getByLabelText("JPY")).toBeDefined();
    });
    await user.click(within(group).getByLabelText("USD"));
    await user.click(within(group).getByLabelText("JPY"));
    await user.click(screen.getByRole("button", { name: "Save active lists" }));

    await waitFor(() => {
      expect(patchSpy).toHaveBeenCalledWith("/workspaces/current", {
        active_currencies: ["EUR", "JPY"],
        active_countries: ["CZ", "DE"],
        active_distributors: ["DigiKey", "Mouser"],
      });
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ws", "ws-1", "ws", "current"] });
    expect(toast.success).toHaveBeenCalledWith("Active lists saved.");
  });
});
