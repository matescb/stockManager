/**
 * Focus management for the five modals that used to hand-roll their backdrop.
 *
 * `components/Modal.tsx` has had a correct focus implementation all along —
 * it records `document.activeElement`, moves focus into the dialog, cycles
 * Tab/Shift-Tab inside it, closes on Escape, and restores focus to the
 * trigger on unmount. But five dialogs never went through it. They each
 * re-typed the same `fixed inset-0 … bg-black/40` backdrop string and got
 * none of that behaviour: no trap, no Escape, no focus restore. 5 of 12
 * modals in the app were keyboard-inaccessible.
 *
 *   routes/projects/detail/AddPartFromLibraryModal.tsx
 *   routes/projects/detail/BomProviderAmbiguityModal.tsx
 *   routes/parts/detail/CreateOrderLineModal.tsx
 *   routes/parts/detail/ReplaceInProjectsModal.tsx
 *   routes/sourcing/alerts/AlertFormModal.tsx
 *
 * This suite is the regression: each of the five is opened from a real
 * trigger button and must trap Tab, close on Escape, and hand focus back to
 * the trigger when it closes.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import AddPartFromLibraryModal from "@/routes/projects/detail/AddPartFromLibraryModal";
import BomProviderAmbiguityModal from "@/routes/projects/detail/BomProviderAmbiguityModal";
import { CreateOrderLineModal } from "@/routes/parts/detail/CreateOrderLineModal";
import ReplaceInProjectsModal from "@/routes/parts/detail/ReplaceInProjectsModal";
import AlertFormModal from "@/routes/sourcing/alerts/AlertFormModal";
import type { Part } from "@/types";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), message: vi.fn() },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const part = {
  id: "11111111-1111-4111-8111-111111111111",
  part_type: "linked",
  name: "STM32",
  manufacturer: "ST",
  mpn: "STM32F103C8T6",
  internal_part_number: null,
  description: null,
  footprint: null,
  notes_markdown: null,
  low_stock_report_quantity: null,
  attrition_percentage: 0,
  attrition_min_quantity: 0,
  default_storage_location_id: null,
  default_storage_mandatory: false,
  serialized: false,
  published: false,
  linked_provider: null,
  linked_external_id: null,
  last_refresh_at: null,
  description_locally_edited: false,
  archived_at: null,
  on_hand: 0,
  reserved: 0,
  available: 0,
  image_url: null,
} as unknown as Part;

/** Mirrors Modal.tsx's own focusable query so the trap can be checked generically. */
function focusables(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(
    [
      "a[href]",
      "button:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      "[tabindex]:not([tabindex='-1'])",
    ].join(","),
  )).filter(el => el.getAttribute("aria-hidden") !== "true");
}

/** Renders `children(open, close)` behind a real trigger button. */
function Harness({ children }: { children: (open: boolean, close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Open dialog</button>
      {children(open, () => setOpen(false))}
    </>
  );
}

function renderHarness(children: (open: boolean, close: () => void) => ReactNode) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <Harness>{children}</Harness>
    </QueryClientProvider>,
  );
}

type Case = {
  name: string;
  file: string;
  render: (open: boolean, close: () => void) => ReactNode;
};

const CASES: Case[] = [
  {
    name: "AddPartFromLibraryModal",
    file: "routes/projects/detail/AddPartFromLibraryModal.tsx",
    render: (open, close) => (
      <AddPartFromLibraryModal open={open} projectId="project-1" onClose={close} />
    ),
  },
  {
    name: "BomProviderAmbiguityModal",
    file: "routes/projects/detail/BomProviderAmbiguityModal.tsx",
    render: (open, close) => (
      <BomProviderAmbiguityModal
        open={open}
        choices={[{
          entry_id: "entry-1",
          mpn: "STM32F103C8T6",
          candidates: [
            { manufacturer: "ST", mpn: "STM32F103C8T6", description: null, source_url: null, image_url: null },
            { manufacturer: "GD", mpn: "GD32F103C8T6", description: null, source_url: null, image_url: null },
          ],
        }]}
        onClose={close}
        onConfirm={vi.fn()}
      />
    ),
  },
  {
    name: "CreateOrderLineModal",
    file: "routes/parts/detail/CreateOrderLineModal.tsx",
    render: (open, close) => (
      <CreateOrderLineModal
        open={open}
        source={{
          partId: part.id,
          distributor: "DigiKey",
          packaging: "Tape",
          leadTimeDays: 3,
          fetchedAt: "2026-05-08T12:00:00+00:00",
          quantity: 25,
          unitPrice: 1.23,
          currency: "EUR",
          productUrl: "https://www.trustedparts.com/digikey/stm32",
        }}
        onClose={close}
      />
    ),
  },
  {
    name: "ReplaceInProjectsModal",
    file: "routes/parts/detail/ReplaceInProjectsModal.tsx",
    render: (open, close) => (
      <ReplaceInProjectsModal open={open} part={part} onClose={close} />
    ),
  },
  {
    name: "AlertFormModal",
    file: "routes/sourcing/alerts/AlertFormModal.tsx",
    render: (open, close) => (
      <AlertFormModal
        open={open}
        title="Set BOM-buyable alert"
        initialValues={{ alert_type: "bom_buyable", project_id: "project-1", build_quantity: 1 }}
        allowedTypes={["bom_buyable"]}
        onClose={close}
      />
    ),
  },
];

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.spyOn(api, "get").mockResolvedValue([] as never);
  vi.spyOn(api, "post").mockResolvedValue({} as never);
  vi.spyOn(api, "patch").mockResolvedValue({} as never);
});

describe.each(CASES)("$name focus management", ({ render: renderModal, file }) => {
  it(`moves focus into the dialog on open (${file})`, async () => {
    const user = userEvent.setup();
    renderHarness(renderModal);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
  });

  it(`cycles Shift+Tab from the first focusable to the last (${file})`, async () => {
    const user = userEvent.setup();
    renderHarness(renderModal);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));

    const inDialog = focusables(dialog);
    expect(inDialog.length).toBeGreaterThan(1);
    inDialog[0].focus();

    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });

    // Without the trap, Shift+Tab from the first control walks out of the
    // dialog and onto the page behind it.
    expect(document.activeElement).toBe(inDialog[inDialog.length - 1]);
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it(`closes on Escape and restores focus to the trigger (${file})`, async () => {
    const user = userEvent.setup();
    renderHarness(renderModal);

    const trigger = screen.getByRole("button", { name: "Open dialog" });
    await user.click(trigger);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});
