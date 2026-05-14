import { describe, expect, it, vi } from "vitest";

vi.mock("@sentry/react", () => ({
  init: vi.fn(),
  reactRouterV6BrowserTracingIntegration: vi.fn(() => ({ name: "router" })),
  replayIntegration: vi.fn(() => ({ name: "replay" })),
}));

describe("Sentry beforeSend", () => {
  it("test_beforesend_strips_query_string", async () => {
    vi.resetModules();

    const Sentry = await import("@sentry/react");
    vi.mocked(Sentry.init).mockClear();

    await import("./instrument");

    const options = vi.mocked(Sentry.init).mock.calls[0]?.[0];
    const beforeSend = options?.beforeSend;
    expect(beforeSend).toBeTypeOf("function");
    if (!beforeSend) {
      throw new Error("Sentry beforeSend was not configured");
    }

    const event = {
      type: undefined,
      request: {
        url: "https://parts.matescb.cz/parts/abc?token=secret&workspace_id=ws#stock?tab=lots",
        query_string: "token=secret&workspace_id=ws",
        method: "GET",
        headers: {
          Referer: "https://parts.matescb.cz/search?q=secret#results?sort=qty",
        },
      },
      breadcrumbs: [
        {
          category: "fetch",
          data: {
            url: "/api/parts?search=secret",
            to: "/projects?workspace=secret#bom?line=1",
            method: "GET",
          },
        },
      ],
    } as Parameters<typeof beforeSend>[0];

    const scrubbed = await Promise.resolve(beforeSend(event, {}));

    expect(scrubbed?.request?.url).toBe("https://parts.matescb.cz/parts/abc#stock");
    expect(scrubbed?.request?.query_string).toBeUndefined();
    expect(scrubbed?.request?.headers?.Referer).toBe(
      "https://parts.matescb.cz/search#results",
    );
    expect(scrubbed?.breadcrumbs?.[0]?.data?.url).toBe("/api/parts");
    expect(scrubbed?.breadcrumbs?.[0]?.data?.to).toBe("/projects#bom");
    expect(JSON.stringify(scrubbed)).not.toContain("secret");
    expect(JSON.stringify(scrubbed)).not.toContain("?");
  });
});
