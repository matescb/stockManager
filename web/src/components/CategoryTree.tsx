/**
 * A KiCad-library-style category rail: expand/collapse, keyboard-navigable,
 * one selected node, selection lifted to the caller (which puts it in the
 * URL so it is deep-linkable).
 *
 * Accessibility follows the ARIA tree pattern, which is the reason for the
 * roving tabindex: exactly one node is in the tab order at a time, so Tab
 * moves past the whole rail rather than through every category. Within it,
 * Up/Down walk the *visible* rows (not the DOM order of a collapsed
 * subtree), Right expands or descends, Left collapses or climbs, Home/End
 * jump to the ends, and Enter/Space select. That is what a desktop tree
 * does, and this rail stands in for one in a KiCad-shaped workflow.
 *
 * Expansion state is per-workspace localStorage, keyed the same way
 * `dataTableStorageKey` keys column visibility. It is a view preference,
 * not data: a corrupt or missing entry silently falls back to "roots
 * expanded", never an error.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, FolderTree } from "lucide-react";

import { useAuth } from "@/lib/auth";
import {
  ancestorIds,
  buildCategoryTree,
  visibleNodes,
  type CategoryTreeNode,
  type TreeNodeInput,
} from "@/lib/categoryTree";

export type CategoryTreeRow = TreeNodeInput;

export function categoryTreeStorageKey(
  workspaceId: string | null | undefined,
  treeId: string,
): string {
  return `ws:${workspaceId ?? "none"}:tree:${treeId}`;
}

function loadExpanded(storageKey: string): string[] | null {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((v): v is string => typeof v === "string");
  } catch {
    return null;
  }
}

function saveExpanded(storageKey: string, ids: ReadonlySet<string>): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify([...ids]));
  } catch {
    // Private-mode / quota. A lost view preference is not worth an error.
  }
}

type Props = {
  rows: readonly CategoryTreeRow[];
  /** Selected category id, or null for "All". Owned by the caller. */
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Optional per-category count shown as a trailing badge. */
  countFor?: (id: string) => number | undefined;
  /** Distinguishes localStorage entries when two trees coexist. */
  treeId?: string;
  /** Label for the "no filter" row at the top. */
  allLabel?: string;
  className?: string;
};

export default function CategoryTree({
  rows,
  selectedId,
  onSelect,
  countFor,
  treeId = "categories",
  allLabel = "All parts",
  className,
}: Props) {
  const { workspaceId } = useAuth();
  const storageKey = categoryTreeStorageKey(workspaceId, treeId);

  const tree = useMemo(() => buildCategoryTree(rows), [rows]);

  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);

  // Hydrate once per workspace: prefer the saved set; with no saved
  // preference, default to expanding the roots so a first-time user sees
  // the top level rather than a wall of collapsed chevrons.
  //
  // The `rows.length === 0` bail is load-bearing. `rows` normally arrives
  // from an async query, so the first render has none — and defaulting off
  // an empty tree would compute "expand these zero roots", mark itself
  // hydrated, and leave every branch collapsed once the real rows landed.
  // A saved set needs no rows, so it is applied immediately.
  useEffect(() => {
    if (hydratedFor === storageKey) return;
    const saved = loadExpanded(storageKey);
    if (saved !== null) {
      setExpanded(new Set(saved));
      setHydratedFor(storageKey);
      return;
    }
    if (rows.length === 0) return;
    setExpanded(new Set(tree.map((n) => n.node.id)));
    setHydratedFor(storageKey);
  }, [storageKey, hydratedFor, tree, rows.length]);

  // A selection that arrives from the URL may sit inside a collapsed
  // branch — reveal it rather than showing an empty-looking rail.
  useEffect(() => {
    if (!selectedId || hydratedFor !== storageKey) return;
    const missing = ancestorIds(rows, selectedId);
    if (missing.length === 0) return;
    setExpanded((prev) => {
      if (missing.every((id) => prev.has(id))) return prev;
      const next = new Set(prev);
      for (const id of missing) next.add(id);
      saveExpanded(storageKey, next);
      return next;
    });
  }, [selectedId, rows, storageKey, hydratedFor]);

  const visible = useMemo(() => visibleNodes(tree, expanded), [tree, expanded]);

  // Roving tabindex: `focusId` is the one node reachable by Tab. It starts
  // on the selection so returning to the rail resumes where you were.
  const [focusId, setFocusId] = useState<string | null>(null);
  // A ref map rather than a `querySelector` by id: category ids are UUIDs
  // today, but building a selector from data means depending on
  // `CSS.escape`, which is missing in some environments (jsdom included)
  // and is a needless injection surface if ids ever stop being UUIDs.
  const nodeRefs = useRef(new Map<string, HTMLElement>());

  const activeId =
    focusId && visible.some((n) => n.node.id === focusId)
      ? focusId
      : (selectedId && visible.some((n) => n.node.id === selectedId)
          ? selectedId
          : visible[0]?.node.id ?? null);

  const focusNode = useCallback((id: string) => {
    setFocusId(id);
    nodeRefs.current.get(id)?.focus();
  }, []);

  const toggle = useCallback(
    (id: string, open?: boolean) => {
      setExpanded((prev) => {
        const shouldOpen = open ?? !prev.has(id);
        if (shouldOpen === prev.has(id)) return prev;
        const next = new Set(prev);
        if (shouldOpen) next.add(id);
        else next.delete(id);
        saveExpanded(storageKey, next);
        return next;
      });
    },
    [storageKey],
  );

  function onKeyDown(event: React.KeyboardEvent, entry: CategoryTreeNode<CategoryTreeRow>) {
    const index = visible.findIndex((n) => n.node.id === entry.node.id);
    const hasChildren = entry.children.length > 0;
    const isOpen = expanded.has(entry.node.id);

    switch (event.key) {
      case "ArrowDown": {
        event.preventDefault();
        const next = visible[index + 1];
        if (next) focusNode(next.node.id);
        return;
      }
      case "ArrowUp": {
        event.preventDefault();
        const prev = visible[index - 1];
        if (prev) focusNode(prev.node.id);
        return;
      }
      case "ArrowRight": {
        event.preventDefault();
        if (hasChildren && !isOpen) toggle(entry.node.id, true);
        else if (hasChildren) focusNode(entry.children[0].node.id);
        return;
      }
      case "ArrowLeft": {
        event.preventDefault();
        if (hasChildren && isOpen) {
          toggle(entry.node.id, false);
          return;
        }
        // Climb to the parent row, which is the nearest earlier visible
        // node one level up.
        for (let i = index - 1; i >= 0; i -= 1) {
          if (visible[i].depth < entry.depth) {
            focusNode(visible[i].node.id);
            return;
          }
        }
        return;
      }
      case "Home": {
        event.preventDefault();
        if (visible[0]) focusNode(visible[0].node.id);
        return;
      }
      case "End": {
        event.preventDefault();
        const last = visible[visible.length - 1];
        if (last) focusNode(last.node.id);
        return;
      }
      case "Enter":
      case " ": {
        event.preventDefault();
        onSelect(entry.node.id === selectedId ? null : entry.node.id);
        return;
      }
      default:
        break;
    }
  }

  const allSelected = selectedId === null;

  return (
    <nav className={className} aria-label="Categories">
      <button
        type="button"
        onClick={() => onSelect(null)}
        aria-current={allSelected ? "true" : undefined}
        className={[
          "w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-left transition-colors",
          allSelected
            ? "bg-accent/15 text-accent font-medium"
            : "text-text hover:bg-panel2",
        ].join(" ")}
      >
        <FolderTree size={14} className="shrink-0 text-muted" />
        {allLabel}
      </button>

      {rows.length === 0 ? (
        <p className="px-2 py-3 text-xs text-muted">
          No categories yet. Create them in Settings → Categories to file
          parts into a tree.
        </p>
      ) : (
        <div role="tree" aria-label="Part categories" className="mt-1">
          {visible.map((entry) => {
            const { node, depth, children } = entry;
            const isSelected = node.id === selectedId;
            const hasChildren = children.length > 0;
            const isOpen = expanded.has(node.id);
            const count = countFor?.(node.id);
            return (
              <div
                key={node.id}
                role="treeitem"
                ref={(el) => {
                  if (el) nodeRefs.current.set(node.id, el);
                  else nodeRefs.current.delete(node.id);
                }}
                aria-level={depth + 1}
                aria-selected={isSelected}
                aria-expanded={hasChildren ? isOpen : undefined}
                tabIndex={node.id === activeId ? 0 : -1}
                onFocus={() => setFocusId(node.id)}
                onKeyDown={(e) => onKeyDown(e, entry)}
                onClick={() => onSelect(isSelected ? null : node.id)}
                className={[
                  "group flex items-center gap-1 rounded-md pr-2 py-1 text-sm cursor-pointer",
                  "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
                  isSelected
                    ? "bg-accent/15 text-accent font-medium"
                    : "text-text hover:bg-panel2",
                ].join(" ")}
                style={{ paddingLeft: `${depth * 14 + 6}px` }}
                title={node.name}
              >
                {hasChildren ? (
                  <button
                    type="button"
                    // The chevron toggles without selecting — clicking a
                    // branch to look inside it is a different intent from
                    // filtering the list to it.
                    onClick={(e) => {
                      e.stopPropagation();
                      toggle(node.id);
                    }}
                    tabIndex={-1}
                    aria-hidden="true"
                    className="shrink-0 rounded p-0.5 text-muted hover:text-text hover:bg-panelHover"
                  >
                    {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  </button>
                ) : (
                  <span className="shrink-0 w-[18px]" aria-hidden="true" />
                )}
                <span className="truncate">{node.name}</span>
                {node.archived_at !== null && (
                  <span className="pill shrink-0 ml-1">Archived</span>
                )}
                {count !== undefined && (
                  <span className="ml-auto shrink-0 text-xs text-muted tabular-nums">
                    {count}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </nav>
  );
}
