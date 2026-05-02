/**
 * DOM tests for ApiError rendering in mutation error states (FE-008 / issue #42).
 *
 * Pinned behaviours:
 *  - When a useMutation handler throws ApiError, the error message is
 *    rendered visibly to the user
 *  - A 409 conflict error with existing_id renders a link to the
 *    conflicting part at /parts/<id>
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useApiMutation } from "@/lib/mutations";
import { ApiError } from "@/lib/api";
import { useState } from "react";

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function makeClient() {
  return new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
}

function Wrapper({ children, client }: { children: React.ReactNode; client: QueryClient }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/**
 * Minimal harness that runs a mutation and displays the error message when
 * the mutation fails. Mirrors the pattern in PartSettings / other forms.
 */
function MutationErrorHarness({
  mutationFn,
}: {
  mutationFn: () => Promise<unknown>;
}) {
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [conflictId, setConflictId] = useState<string | null>(null);

  const m = useApiMutation<unknown, void>({
    mutationKey: ["test-mutation"],
    mutationFn,
    onError: (e) => {
      if (e instanceof ApiError) {
        setErrMsg(e.message);
        const existingId = (e.body as ({ existing_id?: string } | null))?.existing_id ?? null;
        if (existingId) setConflictId(existingId);
      } else {
        setErrMsg("Unknown error");
      }
    },
  });

  return (
    <div>
      <button onClick={() => m.mutate()}>submit</button>
      {errMsg && <div role="alert">{errMsg}</div>}
      {conflictId && (
        <a href={`/parts/${conflictId}`}>View conflicting part</a>
      )}
    </div>
  );
}

describe("ApiError rendering", () => {
  it("renders the ApiError message when a mutation throws", async () => {
    const client = makeClient();
    render(
      <Wrapper client={client}>
        <MutationErrorHarness
          mutationFn={async () => {
            throw new ApiError(
              409,
              { data: null, status: { category: "conflict", message: "MPN collides" } },
              "MPN collides",
            );
          }}
        />
      </Wrapper>,
    );

    fireEvent.click(screen.getByText("submit"));

    await waitFor(() => {
      expect(screen.getByText(/MPN collides/)).toBeDefined();
    });
  });

  it("renders a link to /parts/<id> when a 409 includes existing_id", async () => {
    const EXISTING_ID = "part-uuid-123";
    const client = makeClient();

    render(
      <Wrapper client={client}>
        <MutationErrorHarness
          mutationFn={async () => {
            // The server spreads extra fields like existing_id onto the
            // response body (CLAUDE.md: API envelope). Cast via unknown
            // to smuggle the extra field past TypeScript's ApiErr type.
            const body = {
              data: null as null,
              status: { category: "conflict", message: "MPN already in use" },
              existing_id: EXISTING_ID,
            };
            throw new ApiError(409, body as unknown as null, "MPN already in use");
          }}
        />
      </Wrapper>,
    );

    fireEvent.click(screen.getByText("submit"));

    await waitFor(() => {
      expect(screen.getByText(/MPN already in use/)).toBeDefined();
    });

    const link = screen.getByRole("link", { name: /view conflicting part/i });
    expect(link).toBeDefined();
    expect((link as HTMLAnchorElement).href).toContain(`/parts/${EXISTING_ID}`);
  });

  it("renders the error message in an accessible alert element", async () => {
    const client = makeClient();
    render(
      <Wrapper client={client}>
        <MutationErrorHarness
          mutationFn={async () => {
            throw new ApiError(
              422,
              { data: null, status: { category: "validation_error", message: "Name is required" } },
              "Name is required",
            );
          }}
        />
      </Wrapper>,
    );

    fireEvent.click(screen.getByText("submit"));

    await waitFor(() => {
      const alert = screen.getByRole("alert");
      expect(alert.textContent).toContain("Name is required");
    });
  });
});
