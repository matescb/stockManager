import { describe, expect, it } from "vitest";

import {
  ancestorIds,
  buildCategoryTree,
  categoryPath,
  descendantIds,
  flattenTree,
  visibleNodes,
  type TreeNodeInput,
} from "@/lib/categoryTree";

function row(
  id: string,
  name: string,
  parent_id: string | null = null,
  sort_order = 0,
  archived_at: string | null = null,
): TreeNodeInput {
  return { id, name, parent_id, sort_order, archived_at };
}

/** passives → {capacitors, resistors → thin-film}, actives */
const rows: TreeNodeInput[] = [
  row("passives", "Passives"),
  row("actives", "Actives"),
  row("resistors", "Resistors", "passives"),
  row("capacitors", "Capacitors", "passives"),
  row("thin-film", "Thin film", "resistors"),
];

const names = (nodes: { node: TreeNodeInput }[]) => nodes.map((n) => n.node.name);

describe("buildCategoryTree", () => {
  it("nests children under their parent", () => {
    const tree = buildCategoryTree(rows);
    expect(names(tree)).toEqual(["Actives", "Passives"]);

    const passives = tree.find((n) => n.node.id === "passives");
    expect(names(passives?.children ?? [])).toEqual(["Capacitors", "Resistors"]);
    expect(passives?.children[1].children[0].node.name).toBe("Thin film");
  });

  it("assigns depth from zero at the roots", () => {
    const flat = flattenTree(buildCategoryTree(rows));
    const depths = Object.fromEntries(flat.map((n) => [n.node.id, n.depth]));
    expect(depths).toEqual({
      actives: 0,
      passives: 0,
      capacitors: 1,
      resistors: 1,
      "thin-film": 2,
    });
  });

  it("orders siblings by sort_order, then name", () => {
    const tree = buildCategoryTree([
      row("b", "Bravo", null, 10),
      row("a", "Alpha", null, 20),
      row("c", "Charlie", null, 10),
    ]);
    expect(names(tree)).toEqual(["Bravo", "Charlie", "Alpha"]);
  });

  it("treats a row whose parent is absent from the input as a root", () => {
    // e.g. the parent is archived and was filtered out upstream. Dropping
    // the child instead would make a category invisible.
    const tree = buildCategoryTree([row("orphan", "Orphan", "not-in-this-list")]);
    expect(names(tree)).toEqual(["Orphan"]);
    expect(tree[0].depth).toBe(0);
  });

  it("survives a self-parent without recursing forever", () => {
    const tree = buildCategoryTree([row("loop", "Loop", "loop")]);
    expect(names(tree)).toEqual(["Loop"]);
    expect(tree[0].children).toEqual([]);
  });

  it("renders every row even when two form a cycle", () => {
    // A cycle has no root, so a naive builder emits nothing at all. The
    // server refuses to write one, but restored backups and the MCP
    // surface are not this module's to trust.
    const tree = buildCategoryTree([
      row("a", "A", "b"),
      row("b", "B", "a"),
      row("ok", "Ok"),
    ]);
    expect(flattenTree(tree).map((n) => n.node.id).sort()).toEqual([
      "a",
      "b",
      "ok",
    ]);
  });
});

describe("visibleNodes", () => {
  const tree = buildCategoryTree(rows);

  it("shows only roots when nothing is expanded", () => {
    expect(names(visibleNodes(tree, new Set()))).toEqual(["Actives", "Passives"]);
  });

  it("reveals a level per expanded ancestor", () => {
    expect(names(visibleNodes(tree, new Set(["passives"])))).toEqual([
      "Actives",
      "Passives",
      "Capacitors",
      "Resistors",
    ]);
    expect(names(visibleNodes(tree, new Set(["passives", "resistors"])))).toEqual([
      "Actives",
      "Passives",
      "Capacitors",
      "Resistors",
      "Thin film",
    ]);
  });

  it("hides a grandchild whose intermediate parent is collapsed", () => {
    expect(names(visibleNodes(tree, new Set(["resistors"])))).toEqual([
      "Actives",
      "Passives",
    ]);
  });
});

describe("descendantIds", () => {
  it("includes the node itself", () => {
    expect(descendantIds(rows, "thin-film")).toEqual(new Set(["thin-film"]));
  });

  it("collects a whole subtree", () => {
    expect(descendantIds(rows, "passives")).toEqual(
      new Set(["passives", "resistors", "capacitors", "thin-film"]),
    );
  });

  it("does not cross into a sibling branch", () => {
    expect(descendantIds(rows, "actives")).toEqual(new Set(["actives"]));
  });

  it("terminates on a cycle", () => {
    expect(descendantIds([row("a", "A", "b"), row("b", "B", "a")], "a")).toEqual(
      new Set(["a", "b"]),
    );
  });
});

describe("ancestorIds", () => {
  it("returns nearest parent first", () => {
    expect(ancestorIds(rows, "thin-film")).toEqual(["resistors", "passives"]);
  });

  it("is empty for a root", () => {
    expect(ancestorIds(rows, "passives")).toEqual([]);
  });

  it("terminates on a cycle", () => {
    expect(ancestorIds([row("a", "A", "b"), row("b", "B", "a")], "a")).toEqual([
      "b",
    ]);
  });
});

describe("categoryPath", () => {
  it("joins the ancestors root-first", () => {
    expect(categoryPath(rows, "thin-film")).toBe("Passives / Resistors / Thin film");
  });

  it("is just the name for a root", () => {
    expect(categoryPath(rows, "actives")).toBe("Actives");
  });

  it("is empty for an unknown id", () => {
    expect(categoryPath(rows, "nope")).toBe("");
  });
});
