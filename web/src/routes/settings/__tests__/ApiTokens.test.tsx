// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { api } from "@/lib/api";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";
import type { ApiToken, ApiTokenCreated } from "@/types";
import ApiTokensSettings from "../ApiTokens";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    workspaceId: "ws-1",
    me: { user: { id: "user-1", email: "me@x.com", name: "Me" }, workspaces: [] },
  }),
}));

const tokens: ApiToken[] = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    label: "KiCad laptop",
    read_only: true,
    created_at: "2026-08-01T10:00:00+00:00",
    expires_at: null,
    revoked_at: null,
    last_used_at: "2026-08-20T09:00:00+00:00",
    user_email: null,
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    label: "Old script",
    read_only: false,
    created_at: "2026-07-01T10:00:00+00:00",
    expires_at: "2026-12-01T10:00:00+00:00",
    revoked_at: "2026-08-15T10:00:00+00:00",
    last_used_at: null,
    user_email: null,
  },
];

const minted: ApiTokenCreated = {
  id: "33333333-3333-4333-8333-333333333333",
  label: "Agent",
  read_only: false,
  created_at: "2026-09-01T10:00:00+00:00",
  expires_at: null,
  revoked_at: null,
  last_used_at: null,
  user_email: null,
  token: "smk_33333333333343338333333333333333.s3cr3t-plaintext-value",
};

function renderTokens() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ConfirmDialogProvider>
        <MemoryRouter initialEntries={["/settings/api-tokens"]}>
          <ApiTokensSettings />
        </MemoryRouter>
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

describe("ApiTokensSettings", () => {
  it("renders each token with its read-only and revoked pills", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(tokens);
    vi.spyOn(api, "get").mockResolvedValue([{ user_id: "user-1", role: "member" }]);

    renderTokens();

    const table = await screen.findByRole("table");
    expect(within(table).getByText("KiCad laptop")).toBeDefined();
    expect(within(table).getByText("Read-only")).toBeDefined();
    expect(within(table).getByText("Revoked")).toBeDefined();
    // A token with no expiry reads "Never", not an empty cell.
    expect(within(table).getByText("Never")).toBeDefined();
    // The revoked row offers no Revoke action.
    expect(within(table).getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
  });

  it("hides the admin-only 'show everyone's tokens' toggle from a member", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(tokens);
    vi.spyOn(api, "get").mockResolvedValue([{ user_id: "user-1", role: "member" }]);

    renderTokens();

    await screen.findByRole("table");
    expect(screen.queryByLabelText("Show everyone's tokens")).toBeNull();
  });

  it("posts the create form and shows the plaintext exactly once", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue([]);
    vi.spyOn(api, "get").mockResolvedValue([{ user_id: "user-1", role: "admin" }]);
    const post = vi.spyOn(api, "post").mockResolvedValue(minted);

    renderTokens();

    fireEvent.click(await screen.findByRole("button", { name: "+ Token" }));
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Agent" } });
    fireEvent.change(screen.getByLabelText("Expires in (days)"), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith("/tokens", {
      label: "Agent",
      read_only: false,
      expires_in_days: 90,
    });

    const shown = await screen.findByTestId("minted-token");
    expect(shown.textContent).toBe(minted.token);

    // Dismissing it takes the plaintext off the page for good — it is
    // never re-read from the query cache.
    fireEvent.click(screen.getByRole("button", { name: "I've saved it" }));
    await waitFor(() => expect(screen.queryByTestId("minted-token")).toBeNull());
  });

  it("refuses to submit without a label and never calls the API", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue([]);
    vi.spyOn(api, "get").mockResolvedValue([]);
    const post = vi.spyOn(api, "post").mockResolvedValue(minted);

    renderTokens();

    fireEvent.click(await screen.findByRole("button", { name: "+ Token" }));
    fireEvent.click(await screen.findByRole("button", { name: "Create" }));

    expect(await screen.findByText("Label is required.")).toBeDefined();
    expect(post).not.toHaveBeenCalled();
  });

  it("rejects an out-of-range expiry before it reaches the server", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue([]);
    vi.spyOn(api, "get").mockResolvedValue([]);
    const post = vi.spyOn(api, "post").mockResolvedValue(minted);

    renderTokens();

    fireEvent.click(await screen.findByRole("button", { name: "+ Token" }));
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Agent" } });
    fireEvent.change(screen.getByLabelText("Expires in (days)"), { target: { value: "9999" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(
      await screen.findByText("Expiry must be a whole number of days between 1 and 365."),
    ).toBeDefined();
    expect(post).not.toHaveBeenCalled();
  });
});
