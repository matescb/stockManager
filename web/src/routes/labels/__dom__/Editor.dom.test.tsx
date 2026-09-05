/**
 * DOM tests for the label designer's editor.
 *
 * What these pin:
 *  - the canvas renders one node per element, positioned in mm x zoom;
 *  - the palette adds an element and selects it, and the property panel
 *    edits it immutably;
 *  - a keyboard nudge moves the selected element (the designer has to be
 *    usable without a pointer);
 *  - Save sends the geometry AND the serialisation rules the renderer
 *    depends on — no designer-local `id`, no `entity_type` on PATCH, and a
 *    bound field with no literal `text` key;
 *  - Test print on an unsaved/dirty draft refuses locally instead of
 *    posting a stale template id;
 *  - a printer failure on Test print renders as a recorded-job message.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import Editor from "../Editor";
import { starterTemplate, toDraft } from "../factory";
import { TemplateSchema } from "../types";

const parsedGet = vi.fn();
const parsedPost = vi.fn();
const parsedPatch = vi.fn();
const toastError = vi.fn();
const toastSuccess = vi.fn();

vi.mock("sonner", () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a),
    success: (...a: unknown[]) => toastSuccess(...a),
  },
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ workspaceId: "ws-1" }) }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      parsed: {
        ...actual.api.parsed,
        get: (...args: unknown[]) => parsedGet(...args),
        post: (...args: unknown[]) => parsedPost(...args),
        patch: (...args: unknown[]) => parsedPatch(...args),
      },
    },
  };
});

const SAVED = TemplateSchema.parse({
  id: "tpl-1",
  name: "Part label",
  entity_type: "part",
  width_mm: 50,
  height_mm: 30,
  gap_mm: 3,
  heat: 100,
  speed: 0,
  method: "T",
  dpi: 300,
  is_default: true,
  elements: [
    { kind: "qr", x_mm: 2, y_mm: 2, dotsize_mm: 0.5, ec: "M" },
    { kind: "text", x_mm: 25, y_mm: 3, binding: "name", font: 5, size_pt: 9 },
  ],
});

function renderEditor(initial = toDraft(SAVED)) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onSaved = vi.fn();
  const utils = render(
    <QueryClientProvider client={client}>
      <Editor initial={initial} onClose={() => {}} onSaved={onSaved} />
    </QueryClientProvider>,
  );
  return { ...utils, onSaved };
}

/** Select a canvas element the way a pointer does — click alone does not
 *  reach `onPointerDown`, which is what the canvas listens on. */
function selectElement(node: HTMLElement) {
  fireEvent.pointerDown(node);
}

function elementNodes(): HTMLElement[] {
  return screen.getAllByRole("button").filter((node) =>
    (node.getAttribute("aria-label") ?? "").includes("element at"),
  );
}

beforeEach(() => {
  cleanup();
  parsedGet.mockReset();
  parsedPost.mockReset();
  parsedPatch.mockReset();
  toastError.mockReset();
  toastSuccess.mockReset();
});

describe("label Editor", () => {
  it("renders one canvas node per element, positioned in mm x zoom", () => {
    renderEditor();
    const nodes = elementNodes();
    expect(nodes).toHaveLength(2);
    // Default zoom is 4 px/mm, so the text element at x=25 mm sits at 100 px.
    const text = nodes.find((n) => n.getAttribute("aria-label")?.startsWith("text"));
    expect(text?.style.left).toBe("100px");
    expect(text?.style.top).toBe("12px");
  });

  it("adds an element from the palette and selects it", () => {
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Line" }));
    expect(elementNodes()).toHaveLength(3);
    // The property panel switches to the new element's kind.
    expect(screen.getByRole("heading", { name: "Line" })).toBeDefined();
    expect(screen.getByLabelText("Length (mm)")).toHaveProperty("value", "20");
  });

  it("edits the selected element through the property panel", () => {
    renderEditor();
    selectElement(elementNodes()[0]);
    const x = screen.getByLabelText("X (mm)");
    fireEvent.change(x, { target: { value: "7" } });
    expect(elementNodes()[0].style.left).toBe("28px");
  });

  it("nudges the selected element with the arrow keys", () => {
    renderEditor();
    const node = elementNodes()[0];
    selectElement(node);
    fireEvent.keyDown(node, { key: "ArrowRight" });
    // 2 mm + 1 mm grid step = 3 mm = 12 px at 4 px/mm.
    expect(elementNodes()[0].style.left).toBe("12px");
  });

  it("removes an element from the property panel", () => {
    renderEditor();
    selectElement(elementNodes()[0]);
    fireEvent.click(screen.getByRole("button", { name: /Remove/ }));
    expect(elementNodes()).toHaveLength(1);
  });

  it("PATCHes a saved template without entity_type or element ids", async () => {
    parsedPatch.mockResolvedValue(SAVED);
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: /Save/ }));

    await waitFor(() => expect(parsedPatch).toHaveBeenCalled());
    const [path, , body] = parsedPatch.mock.calls[0] as [string, unknown, Record<string, unknown>];
    expect(path).toBe("/label-templates/tpl-1");
    expect(body).not.toHaveProperty("entity_type");
    const elements = body.elements as Array<Record<string, unknown>>;
    expect(elements.every((el) => !("id" in el))).toBe(true);
    // A bound field must carry NO `text` key at all — `""` would make the
    // renderer print an empty field instead of resolving the binding.
    const bound = elements.find((el) => el.binding === "name");
    expect(bound).toBeDefined();
    expect(bound).not.toHaveProperty("text");
  });

  it("POSTs a new template and refuses to save it unnamed", async () => {
    parsedPost.mockResolvedValue(SAVED);
    renderEditor(starterTemplate("part"));

    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Give the template a name");
    expect(parsedPost).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Bin label" } });
    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() => expect(parsedPost).toHaveBeenCalled());
    const [path, , body] = parsedPost.mock.calls[0] as [string, unknown, Record<string, unknown>];
    expect(path).toBe("/label-templates");
    expect(body.entity_type).toBe("part");
    expect(body.name).toBe("Bin label");
  });

  it("refuses to test print an unsaved draft instead of posting a stale id", async () => {
    renderEditor(starterTemplate("part"));
    fireEvent.click(screen.getByRole("button", { name: /Test print/ }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Save the template before test printing");
    expect(parsedPost).not.toHaveBeenCalled();
  });

  it("reports a printer failure on test print as a recorded job", async () => {
    parsedPost.mockRejectedValue(
      new ApiError(
        409,
        {
          data: null,
          status: { category: "conflict", message: "the label printer is unreachable" },
          code: "printer.unreachable",
          print_job_id: "job-7",
        } as never,
        "the label printer is unreachable",
      ),
    );
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: /Test print/ }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Printer not configured or unreachable");
    expect(alert.textContent).toContain("job-7");
    expect(toastError).toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("renders the server's JScript for a saved template on demand", async () => {
    parsedGet.mockResolvedValue({ jscript: "m m\nJ\nS l1;0,0,30,30,50\nA 1\n" });
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "JScript" }));

    await waitFor(() => expect(parsedGet).toHaveBeenCalled());
    expect(parsedGet.mock.calls[0][0]).toBe("/label-templates/tpl-1/jscript");
    await waitFor(() => expect(screen.getByText(/S l1;0,0,30,30,50/)).toBeDefined());
  });
});
