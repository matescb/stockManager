// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CategoryTree, {
  categoryTreeStorageKey,
  type CategoryTreeRow,
} from "@/components/CategoryTree";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

function row(
  id: string,
  name: string,
  parent_id: string | null = null,
): CategoryTreeRow {
  return { id, name, parent_id, sort_order: 0, archived_at: null };
}

const rows: CategoryTreeRow[] = [
  row("passives", "Passives"),
  row("actives", "Actives"),
  row("resistors", "Resistors", "passives"),
  row("thin-film", "Thin film", "resistors"),
];

function renderTree(props: Partial<React.ComponentProps<typeof CategoryTree>> = {}) {
  const onSelect = vi.fn();
  render(
    <CategoryTree rows={rows} selectedId={null} onSelect={onSelect} {...props} />,
  );
  return { onSelect };
}

const itemNames = () =>
  screen.queryAllByRole("treeitem").map((el) => el.textContent?.trim());

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

describe("CategoryTree", () => {
  it("expands the roots on first render so the top level is visible", () => {
    renderTree();
    expect(itemNames()).toEqual(["Actives", "Passives", "Resistors"]);
  });

  it("collapses and re-expands a branch on the chevron, without selecting", () => {
    const { onSelect } = renderTree();
    const passives = screen.getByRole("treeitem", { name: /Passives/ });
    expect(passives.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(passives.querySelector("button") as HTMLElement);
    expect(itemNames()).toEqual(["Actives", "Passives"]);
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(passives.querySelector("button") as HTMLElement);
    expect(itemNames()).toEqual(["Actives", "Passives", "Resistors"]);
  });

  it("persists the expanded set per workspace", () => {
    renderTree();
    fireEvent.click(
      screen
        .getByRole("treeitem", { name: /Passives/ })
        .querySelector("button") as HTMLElement,
    );
    const saved = localStorage.getItem(categoryTreeStorageKey("ws-1", "categories"));
    expect(saved).toBeTruthy();
    expect(JSON.parse(saved as string)).not.toContain("passives");
  });

  it("still expands the roots when rows arrive after the first render", () => {
    // The real case: `rows` comes from an async query, so the first render
    // has none. Defaulting off an empty tree would mark itself hydrated
    // and leave every branch collapsed forever.
    const { rerender } = render(
      <CategoryTree rows={[]} selectedId={null} onSelect={vi.fn()} />,
    );
    expect(screen.queryAllByRole("treeitem")).toHaveLength(0);

    rerender(<CategoryTree rows={rows} selectedId={null} onSelect={vi.fn()} />);
    expect(itemNames()).toEqual(["Actives", "Passives", "Resistors"]);
  });

  it("applies a saved set even before rows arrive", () => {
    localStorage.setItem(
      categoryTreeStorageKey("ws-1", "categories"),
      JSON.stringify(["passives", "resistors"]),
    );
    const { rerender } = render(
      <CategoryTree rows={[]} selectedId={null} onSelect={vi.fn()} />,
    );
    rerender(<CategoryTree rows={rows} selectedId={null} onSelect={vi.fn()} />);
    expect(itemNames()).toEqual(["Actives", "Passives", "Resistors", "Thin film"]);
  });

  it("respects a saved empty set rather than re-expanding the roots", () => {
    localStorage.setItem(
      categoryTreeStorageKey("ws-1", "categories"),
      JSON.stringify([]),
    );
    renderTree();
    expect(itemNames()).toEqual(["Actives", "Passives"]);
  });

  it("ignores a corrupt stored value instead of throwing", () => {
    localStorage.setItem(categoryTreeStorageKey("ws-1", "categories"), "{not json");
    renderTree();
    expect(itemNames()).toEqual(["Actives", "Passives", "Resistors"]);
  });

  it("marks the selected node and reports selection back to the caller", () => {
    const { onSelect } = renderTree({ selectedId: "resistors" });
    const resistors = screen.getByRole("treeitem", { name: /Resistors/ });
    expect(resistors.getAttribute("aria-selected")).toBe("true");
    expect(
      screen.getByRole("treeitem", { name: /Actives/ }).getAttribute("aria-selected"),
    ).toBe("false");

    fireEvent.click(screen.getByRole("treeitem", { name: /Actives/ }));
    expect(onSelect).toHaveBeenCalledWith("actives");
  });

  it("clicking the selected node clears the filter", () => {
    const { onSelect } = renderTree({ selectedId: "actives" });
    fireEvent.click(screen.getByRole("treeitem", { name: /Actives/ }));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("reveals a selection that arrives inside a collapsed branch", () => {
    // The deep-link case: `?category=thin-film` names a node two levels
    // down, and the saved state has everything collapsed.
    localStorage.setItem(
      categoryTreeStorageKey("ws-1", "categories"),
      JSON.stringify([]),
    );
    renderTree({ selectedId: "thin-film" });
    expect(itemNames()).toContain("Thin film");
    expect(
      screen.getByRole("treeitem", { name: /Thin film/ }).getAttribute("aria-selected"),
    ).toBe("true");
  });

  it("exposes aria-level so the nesting is announced", () => {
    renderTree();
    expect(
      screen.getByRole("treeitem", { name: /Passives/ }).getAttribute("aria-level"),
    ).toBe("1");
    expect(
      screen.getByRole("treeitem", { name: /Resistors/ }).getAttribute("aria-level"),
    ).toBe("2");
  });

  describe("keyboard", () => {
    it("keeps exactly one node in the tab order (roving tabindex)", () => {
      renderTree();
      const tabbable = screen
        .getAllByRole("treeitem")
        .filter((el) => el.getAttribute("tabindex") === "0");
      expect(tabbable).toHaveLength(1);
    });

    it("ArrowDown and ArrowUp walk the visible rows", () => {
      renderTree();
      const actives = screen.getByRole("treeitem", { name: /Actives/ });
      actives.focus();
      fireEvent.keyDown(actives, { key: "ArrowDown" });
      expect(document.activeElement).toBe(
        screen.getByRole("treeitem", { name: /Passives/ }),
      );
      fireEvent.keyDown(document.activeElement as HTMLElement, { key: "ArrowUp" });
      expect(document.activeElement).toBe(actives);
    });

    it("ArrowDown skips a collapsed subtree", () => {
      renderTree();
      const passives = screen.getByRole("treeitem", { name: /Passives/ });
      fireEvent.keyDown(passives, { key: "ArrowLeft" }); // collapse
      passives.focus();
      fireEvent.keyDown(passives, { key: "ArrowDown" });
      // Nothing below it any more — focus stays put rather than jumping
      // into a hidden node.
      expect(document.activeElement).toBe(passives);
    });

    it("ArrowRight expands, then moves to the first child", () => {
      renderTree();
      const resistors = screen.getByRole("treeitem", { name: /Resistors/ });
      resistors.focus();
      fireEvent.keyDown(resistors, { key: "ArrowRight" });
      expect(itemNames()).toContain("Thin film");
      fireEvent.keyDown(resistors, { key: "ArrowRight" });
      expect(document.activeElement).toBe(
        screen.getByRole("treeitem", { name: /Thin film/ }),
      );
    });

    it("ArrowLeft collapses, then climbs to the parent", () => {
      renderTree();
      const resistors = screen.getByRole("treeitem", { name: /Resistors/ });
      resistors.focus();
      fireEvent.keyDown(resistors, { key: "ArrowLeft" });
      expect(document.activeElement).toBe(
        screen.getByRole("treeitem", { name: /Passives/ }),
      );
    });

    it("Home and End jump to the ends of the visible list", () => {
      renderTree();
      const passives = screen.getByRole("treeitem", { name: /Passives/ });
      passives.focus();
      fireEvent.keyDown(passives, { key: "End" });
      expect(document.activeElement).toBe(
        screen.getByRole("treeitem", { name: /Resistors/ }),
      );
      fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Home" });
      expect(document.activeElement).toBe(
        screen.getByRole("treeitem", { name: /Actives/ }),
      );
    });

    it("Enter and Space select", () => {
      const { onSelect } = renderTree();
      const actives = screen.getByRole("treeitem", { name: /Actives/ });
      fireEvent.keyDown(actives, { key: "Enter" });
      expect(onSelect).toHaveBeenLastCalledWith("actives");
      fireEvent.keyDown(actives, { key: " " });
      expect(onSelect).toHaveBeenLastCalledWith("actives");
    });
  });

  it("offers a way back to the unfiltered list", () => {
    const { onSelect } = renderTree({ selectedId: "actives" });
    fireEvent.click(screen.getByRole("button", { name: "All parts" }));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("explains itself when the workspace has no categories", () => {
    render(<CategoryTree rows={[]} selectedId={null} onSelect={vi.fn()} />);
    expect(screen.queryByRole("tree")).toBeNull();
    expect(screen.getByText(/No categories yet/)).toBeDefined();
  });
});
