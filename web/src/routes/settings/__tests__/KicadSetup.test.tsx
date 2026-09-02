// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { api } from "@/lib/api";
import type { KicadSetup } from "@/types";
import KicadSetupSettings, { buildHttpLibFile, buildPcmUrl } from "../KicadSetup";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    workspaceId: "ws-1",
    me: { user: { id: "user-1", email: "me@x.com", name: "Me" }, workspaces: [] },
  }),
}));

const setup: KicadSetup = {
  root_url: "https://parts.example.com/kicad-api",
  categories_ttl: 600,
  parts_ttl: 60,
  pcm_repository_url_template:
    "https://parts.example.com/kicad-api/pcm/PASTE_YOUR_READONLY_TOKEN/repository.json",
  pcm_package_identifier: "com.stockmanager.0123456789abcdef0123456789abcdef",
  pcm_spice_path_variable: "STOCKMGR_SPICE",
  pcm_spice_path_value:
    "${KICAD8_3RD_PARTY}/resources/com_stockmanager_0123456789abcdef0123456789abcdef/spice",
  read_only_note: "Only read-only tokens are accepted there.",
  mcp_url: "https://parts.example.com/mcp",
  mcp_note: "Point an MCP client at this URL with `Authorization: Bearer <token>`.",
  example: {
    meta: { version: 1 },
    name: "Bench (stockManager)",
    source: {
      type: "REST_API",
      api_version: "v1",
      root_url: "https://parts.example.com/kicad-api",
      token: "PASTE_YOUR_TOKEN_HERE",
      timeout_parts_seconds: 60,
      timeout_categories_seconds: 600,
    },
  },
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/settings/kicad"]}>
        <KicadSetupSettings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("KicadSetupSettings", () => {
  it("renders every value the setup payload carries", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(setup);

    renderPage();

    expect(await screen.findByText(setup.root_url)).toBeDefined();
    expect(screen.getByText(setup.pcm_package_identifier)).toBeDefined();
    expect(screen.getByText(setup.pcm_spice_path_variable)).toBeDefined();
    expect(screen.getByText(setup.pcm_spice_path_value)).toBeDefined();
    expect(screen.getByText(setup.read_only_note)).toBeDefined();
    // The KiCad menu paths are the part a user actually follows; a
    // rename in KiCad is the only thing that should move them.
    expect(screen.getByText(/Manage Symbol Libraries/)).toBeDefined();
    expect(screen.getByText(/Configure Paths/)).toBeDefined();
    // Both cache windows are shown, so "why is my edit not showing up"
    // has an answer on the page.
    expect(screen.getByText(/60 seconds/)).toBeDefined();
    expect(screen.getByText(/600 seconds/)).toBeDefined();
  });

  it("shows the MCP endpoint only when the server reports one", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(setup);
    renderPage();
    expect(await screen.findByText("https://parts.example.com/mcp")).toBeDefined();

    cleanup();
    vi.spyOn(api.parsed, "get").mockResolvedValue({ ...setup, mcp_url: null });
    renderPage();

    // Wait for the payload to land before asserting the absence, or the
    // assertion passes against a still-loading page.
    await screen.findByText(setup.root_url);
    expect(screen.queryByText("https://parts.example.com/mcp")).toBeNull();
  });

  it("holds the download back until a token is pasted", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(setup);

    renderPage();

    const button = await screen.findByRole("button", {
      name: /Download stockmanager\.kicad_httplib/,
    });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("buildHttpLibFile", () => {
  it("writes meta.version as a JSON number, not a string", () => {
    const file = buildHttpLibFile(setup, "smk_abc.def");

    expect(file).toContain('"version": 1.0');
    // The whole point: a quoted version makes the file unloadable, and
    // JSON.stringify would have collapsed 1.0 to 1.
    expect(file).not.toContain('"version": "');
    expect(JSON.parse(file).meta.version).toBe(1);
  });

  it("merges the pasted token in and leaves the placeholder behind", () => {
    const parsed = JSON.parse(buildHttpLibFile(setup, "smk_abc.def"));

    expect(parsed.source.token).toBe("smk_abc.def");
    expect(parsed.source).toEqual({
      type: "REST_API",
      api_version: "v1",
      root_url: "https://parts.example.com/kicad-api",
      token: "smk_abc.def",
      timeout_parts_seconds: 60,
      timeout_categories_seconds: 600,
    });
    expect(parsed.name).toBe("Bench (stockManager)");
  });

  it("escapes a token that would otherwise break the JSON", () => {
    const file = buildHttpLibFile(setup, 'a"b\\c');
    expect(JSON.parse(file).source.token).toBe('a"b\\c');
  });
});

describe("buildPcmUrl", () => {
  it("substitutes the token for the placeholder segment", () => {
    expect(buildPcmUrl(setup, "smk_abc.def")).toBe(
      "https://parts.example.com/kicad-api/pcm/smk_abc.def/repository.json",
    );
  });

  it("leaves the template intact when no token has been pasted", () => {
    expect(buildPcmUrl(setup, "")).toBe(setup.pcm_repository_url_template);
  });
});
