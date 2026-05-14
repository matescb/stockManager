/* @vitest-environment jsdom */
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

function SlowQuery() {
  useQuery({
    queryKey: ["api-abort-test"],
    queryFn: ({ signal }) => api.get("/slow", { signal }),
  });
  return null;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("api query aborts", () => {
  it("passes TanStack query signals to fetch so unmount aborts in-flight reads", async () => {
    let observedSignal: AbortSignal | undefined;
    const pendingFetch = new Promise<Response>(() => {});
    global.fetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      observedSignal = init?.signal ?? undefined;
      return pendingFetch;
    });

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    const view = render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(SlowQuery),
      ),
    );

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(observedSignal).toBeDefined();
    expect(observedSignal?.aborted).toBe(false);

    view.unmount();

    await waitFor(() => expect(observedSignal?.aborted).toBe(true));
    client.clear();
  });
});
