import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@sentry/react", () => ({
  init: vi.fn(),
  reactRouterV6BrowserTracingIntegration: vi.fn(() => ({ name: "router" })),
  replayIntegration: vi.fn(() => ({ name: "replay" })),
}));

describe("Sentry beforeSend", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

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
    } as unknown as Parameters<typeof beforeSend>[0];

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

  it("test_beforesend_strips_exception_value_secrets", async () => {
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
      message: "provider failed api_key=frontend-key token=invite-token",
      exception: {
        values: [
          {
            type: "Error",
            value: "lookup failed password=plain-pass api_key=provider-key token=raw-token",
            message: 'request failed with "secret":"json-secret"',
          },
        ],
      },
    } as unknown as Parameters<typeof beforeSend>[0];

    const scrubbed = await Promise.resolve(beforeSend(event, {}));
    const serialized = JSON.stringify(scrubbed);

    expect(serialized).not.toContain("plain-pass");
    expect(serialized).not.toContain("provider-key");
    expect(serialized).not.toContain("raw-token");
    expect(serialized).not.toContain("json-secret");
    expect(serialized).not.toContain("frontend-key");
    expect(serialized).not.toContain("invite-token");
    expect(scrubbed?.exception?.values?.[0]?.value).toBe(
      "lookup failed password=[Filtered] api_key=[Filtered] token=[Filtered]",
    );
  });

  it("test_init_uses_env_traces_rate", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_SENTRY_TRACES_SAMPLE_RATE", "0.05");
    vi.stubEnv("VITE_SENTRY_REPLAYS_SESSION_SAMPLE_RATE", "0");
    vi.stubEnv("VITE_SENTRY_REPLAYS_ON_ERROR_SAMPLE_RATE", "0");

    const Sentry = await import("@sentry/react");
    vi.mocked(Sentry.init).mockClear();

    await import("./instrument");

    const options = vi.mocked(Sentry.init).mock.calls[0]?.[0];
    expect(options?.tracesSampleRate).toBe(0.05);
    expect(options?.replaysSessionSampleRate).toBe(0);
    expect(options?.replaysOnErrorSampleRate).toBe(0);
  });

  it("test_init_requires_env_traces_rate_in_prod", async () => {
    vi.resetModules();
    vi.stubEnv("PROD", true);
    vi.stubEnv("VITE_SENTRY_TRACES_SAMPLE_RATE", "");

    await expect(import("./instrument")).rejects.toThrow(
      "VITE_SENTRY_TRACES_SAMPLE_RATE is required",
    );
  });
});
