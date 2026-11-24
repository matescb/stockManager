// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { Attachment } from "@/types";
import PartDatasheetLink from "../PartDatasheetLink";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const PART = { id: "11111111-1111-4111-8111-111111111111", mpn: "LM358DR", name: "LM358" };
const UPSTREAM = "https://www.mouser.com/datasheet/2/lm358.pdf";

const STORED: Attachment = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  object_type: "part",
  object_id: PART.id,
  file_name: "LM358DR.pdf",
  file_type: "datasheet",
  mime_type: "application/pdf",
  size_bytes: 2048,
  created_at: "2026-11-20T10:00:00Z",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderWith(attachments: Attachment[], datasheetUrl: string | null) {
  const getSpy = vi.spyOn(api, "get").mockResolvedValue(attachments as never);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PartDatasheetLink part={PART} datasheetUrl={datasheetUrl} />
    </QueryClientProvider>,
  );
  return getSpy;
}

describe("PartDatasheetLink", () => {
  it("links the stored local copy and keeps the provider URL as a secondary source link", async () => {
    const getSpy = renderWith([STORED], UPSTREAM);

    const primary = await screen.findByRole("link", { name: /^datasheet$/i });
    expect(primary.getAttribute("href")).toBe(`/api/attachments/${STORED.id}/download`);
    expect(primary.getAttribute("target")).toBeNull();
    const source = screen.getByRole("link", { name: /source/i });
    expect(source.getAttribute("href")).toBe(UPSTREAM);
    expect(source.getAttribute("target")).toBe("_blank");
    expect(screen.queryByText(/not downloaded yet/i)).toBeNull();
    expect(getSpy).toHaveBeenCalledWith(
      `/attachments/by-object/part/${PART.id}`,
      expect.anything(),
    );
  });

  it("falls back to the provider URL with a not-downloaded hint when nothing is stored", async () => {
    renderWith([], UPSTREAM);

    expect(await screen.findByText(/not downloaded yet/i)).toBeTruthy();
    const link = screen.getByRole("link", { name: /datasheet/i });
    expect(link.getAttribute("href")).toBe(UPSTREAM);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(screen.queryByRole("link", { name: /source/i })).toBeNull();
  });

  it("renders a stored datasheet even when the part has no datasheet_url", async () => {
    renderWith([STORED], null);
    const primary = await screen.findByRole("link", { name: /^datasheet$/i });
    expect(primary.getAttribute("href")).toBe(`/api/attachments/${STORED.id}/download`);
  });

  it("renders nothing without a stored copy or a URL", async () => {
    const getSpy = renderWith([], null);
    await vi.waitFor(() => expect(getSpy).toHaveBeenCalled());
    expect(screen.queryByRole("link")).toBeNull();
  });
});
