/**
 * Regression for the OrderDetail double-submit bug called out in
 * issue #48 (FE2-006).
 *
 * Pre-fix, `OrderDetail.addEntry` did not gate the Add button on
 * `busy`, so a second click while the network request was still in
 * flight posted a duplicate `order_entries` row — the ledger is
 * append-only, so the user got two lines. Post-fix, the Add button
 * is gated on `addEntryMutation.isPending` and the mutation itself
 * carries a `mutationKey` that tells TanStack to serialise duplicates.
 *
 * This is a *narrow* test that recreates the addEntry hook wiring
 * verbatim — instead of mounting the full OrderDetail (which pulls in
 * Router, AuthProvider, AttachmentsPanel's fetches, etc.). The wrapper
 * itself is already covered by `lib/__dom__/mutations.dom.test.tsx`;
 * this one pins the OrderDetail-specific UI gating shape.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useApiMutation } from "@/lib/mutations";

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("OrderDetail.addEntry — double-submit gate (FE2-006)", () => {
  it("the Add button gated on isPending fires exactly one POST on a double-click", async () => {
    // Stand-in for `api.post(\`/orders/${orderId}/entries\`, payload)` —
    // a slow async fn so the second click overlaps with the first.
    const post = vi.fn(async () => {
      await new Promise((r) => setTimeout(r, 30));
      return { id: "entry-1" };
    });

    function Form() {
      const m = useApiMutation<{ id: string }, { quantity_ordered: number }>({
        mutationKey: ["order", "ord-1", "add-entry"],
        mutationFn: post,
      });
      return (
        <button
          // Same shape as OrderDetail's Add button: disabled on isPending.
          disabled={m.isPending}
          onClick={() => m.mutate({ quantity_ordered: 1 })}
        >
          add
        </button>
      );
    }

    const client = new QueryClient({
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    });
    render(
      <QueryClientProvider client={client}>
        <Form />
      </QueryClientProvider>,
    );

    const btn = screen.getByText("add") as HTMLButtonElement;
    fireEvent.click(btn);

    // Once isPending flips, the button is disabled. The "double-click"
    // is the realistic operator action: a second click while still
    // pending. React's `disabled` prop blocks the synthetic onClick,
    // so `mutate` does not fire.
    await waitFor(() => {
      expect(btn.disabled).toBe(true);
    });
    fireEvent.click(btn);
    fireEvent.click(btn);

    await waitFor(() => {
      expect(btn.disabled).toBe(false);
    });

    expect(post).toHaveBeenCalledTimes(1);
  });
});
