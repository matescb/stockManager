/**
 * Pure adjacency-list helpers for the part-category tree.
 *
 * The server owns the rules — cycles, self-parents and the depth cap are
 * refused by `domain/categories/tree.py` on every write. These functions
 * still assume nothing: rows reach the browser from restored backups, the
 * MCP write surface, and any future importer, and a render helper that can
 * hang the main thread on a malformed row is not worth the branch it saves.
 * Every walk carries a visited set and every unknown `parent_id` is treated
 * as a root rather than dropped, so no category is ever invisible.
 *
 * Kept free of React so it can be unit-tested directly and reused by the
 * parts rail and the settings screen without either importing the other.
 */

/** The subset of a category these helpers need. */
export type TreeNodeInput = {
  id: string;
  name: string;
  parent_id: string | null;
  sort_order: number;
  archived_at: string | null;
};

export type CategoryTreeNode<T extends TreeNodeInput> = {
  node: T;
  /** 0 for a root. */
  depth: number;
  children: CategoryTreeNode<T>[];
};

/** Siblings order by `sort_order`, then name, then id — the same total
 * order the API's `list_categories` uses, with id as the final tiebreak so
 * the render is stable across refetches. */
function compare<T extends TreeNodeInput>(a: T, b: T): number {
  if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
  const byName = a.name.localeCompare(b.name);
  return byName !== 0 ? byName : a.id.localeCompare(b.id);
}

/**
 * Build the forest. Every input row appears exactly once in the result:
 * a row whose `parent_id` is absent from the input (an archived parent
 * filtered out upstream, a partial page, a cycle) becomes a root.
 */
export function buildCategoryTree<T extends TreeNodeInput>(
  rows: readonly T[],
): CategoryTreeNode<T>[] {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const childrenOf = new Map<string, T[]>();
  const roots: T[] = [];

  for (const row of rows) {
    const parentId = row.parent_id;
    // Self-parent and dangling parents both fall through to root.
    if (parentId === null || parentId === row.id || !byId.has(parentId)) {
      roots.push(row);
      continue;
    }
    const bucket = childrenOf.get(parentId);
    if (bucket) bucket.push(row);
    else childrenOf.set(parentId, [row]);
  }

  // A cycle among rows that all have resolvable parents would leave those
  // rows unreachable from any root. `placed` catches that: anything the
  // walk never reached is appended as a root at the end, so the tree is
  // always a complete rendering of the input.
  const placed = new Set<string>();

  const attach = (row: T, depth: number): CategoryTreeNode<T> => {
    placed.add(row.id);
    const kids = (childrenOf.get(row.id) ?? [])
      .filter((child) => !placed.has(child.id))
      .sort(compare);
    return {
      node: row,
      depth,
      children: kids.map((child) => attach(child, depth + 1)),
    };
  };

  const tree = [...roots].sort(compare).map((row) => attach(row, 0));

  // Each unreached row is attached as a root, but attaching one may pull
  // the rest of its cycle in as children — so re-check `placed` on every
  // iteration rather than snapshotting the list, or a cycle's members get
  // rendered twice.
  for (const row of [...rows].sort(compare)) {
    if (!placed.has(row.id)) tree.push(attach(row, 0));
  }
  return tree;
}

/** Depth-first flattening — the order a tree renders top to bottom. */
export function flattenTree<T extends TreeNodeInput>(
  tree: readonly CategoryTreeNode<T>[],
): CategoryTreeNode<T>[] {
  const out: CategoryTreeNode<T>[] = [];
  const walk = (nodes: readonly CategoryTreeNode<T>[]) => {
    for (const n of nodes) {
      out.push(n);
      walk(n.children);
    }
  };
  walk(tree);
  return out;
}

/**
 * The visible rows of a tree given a set of expanded node ids: a node is
 * shown when every one of its ancestors is expanded.
 */
export function visibleNodes<T extends TreeNodeInput>(
  tree: readonly CategoryTreeNode<T>[],
  expanded: ReadonlySet<string>,
): CategoryTreeNode<T>[] {
  const out: CategoryTreeNode<T>[] = [];
  const walk = (nodes: readonly CategoryTreeNode<T>[]) => {
    for (const n of nodes) {
      out.push(n);
      if (expanded.has(n.node.id)) walk(n.children);
    }
  };
  walk(tree);
  return out;
}

/** `id` plus every id beneath it. Mirrors the server's `descendant_ids`. */
export function descendantIds<T extends TreeNodeInput>(
  rows: readonly T[],
  id: string,
): Set<string> {
  const childrenOf = new Map<string, string[]>();
  for (const row of rows) {
    if (row.parent_id === null || row.parent_id === row.id) continue;
    const bucket = childrenOf.get(row.parent_id);
    if (bucket) bucket.push(row.id);
    else childrenOf.set(row.parent_id, [row.id]);
  }
  const out = new Set<string>([id]);
  const queue = [id];
  while (queue.length > 0) {
    const next = queue.pop() as string;
    for (const child of childrenOf.get(next) ?? []) {
      if (!out.has(child)) {
        out.add(child);
        queue.push(child);
      }
    }
  }
  return out;
}

/** Ancestor ids of `id`, nearest parent first. Excludes `id` itself. */
export function ancestorIds<T extends TreeNodeInput>(
  rows: readonly T[],
  id: string,
): string[] {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const out: string[] = [];
  const seen = new Set<string>([id]);
  let current = byId.get(id)?.parent_id ?? null;
  while (current !== null && !seen.has(current)) {
    seen.add(current);
    out.push(current);
    current = byId.get(current)?.parent_id ?? null;
  }
  return out;
}

/** `"Passives / Resistors / Thin film"` — for tooltips and pickers. */
export function categoryPath<T extends TreeNodeInput>(
  rows: readonly T[],
  id: string,
  separator = " / ",
): string {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const self = byId.get(id);
  if (!self) return "";
  const names = ancestorIds(rows, id)
    .map((ancestorId) => byId.get(ancestorId)?.name)
    .filter((n): n is string => Boolean(n))
    .reverse();
  return [...names, self.name].join(separator);
}
