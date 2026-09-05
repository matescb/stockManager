import { useRef } from "react";
import { ChevronDown } from "lucide-react";
import { NavLink, useLocation } from "react-router-dom";
import { cn } from "@/lib/cn";

export type SubNavItem = { to: string; label: string };
/**
 * A collapsed set of tabs behind one strip slot. Every child keeps its own
 * route — grouping only changes how the tab is reached, never where it goes.
 */
export type SubNavGroup = { label: string; items: SubNavItem[] };
export type SubNavEntry = SubNavItem | SubNavGroup;

function isGroup(entry: SubNavEntry): entry is SubNavGroup {
  return "items" in entry;
}

/** Flattens a strip to the routes it can reach. Used by tests and callers. */
export function subNavTargets(entries: SubNavEntry[]): SubNavItem[] {
  return entries.flatMap(entry => (isGroup(entry) ? entry.items : [entry]));
}

const TAB_BASE = "px-3 py-1 rounded-[4px] text-sm whitespace-nowrap transition-colors";
const TAB_ACTIVE = "bg-panel text-text shadow-[0_1px_0_rgb(0_0_0_/_0.04)]";
const TAB_IDLE = "text-muted hover:text-text";

function Tab({ item }: { item: SubNavItem }) {
  return (
    <NavLink
      to={item.to}
      end
      className={({ isActive }) => cn(TAB_BASE, isActive ? TAB_ACTIVE : TAB_IDLE)}
    >
      {item.label}
    </NavLink>
  );
}

function GroupTab({ group }: { group: SubNavGroup }) {
  const details = useRef<HTMLDetailsElement>(null);
  const { pathname } = useLocation();
  const activeChild = group.items.find(item => item.to === pathname);

  return (
    <details ref={details} className="relative" title={group.label}>
      <summary
        className={cn(
          TAB_BASE,
          "flex cursor-pointer list-none items-center gap-1 select-none",
          activeChild ? TAB_ACTIVE : TAB_IDLE,
        )}
      >
        {activeChild ? activeChild.label : group.label}
        <ChevronDown size={12} aria-hidden="true" />
      </summary>
      <div className="absolute left-0 top-full z-20 mt-1 flex min-w-[180px] flex-col gap-0.5 card p-1">
        {group.items.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            end
            onClick={() => details.current?.removeAttribute("open")}
            className={({ isActive }) =>
              cn(TAB_BASE, "text-left", isActive ? TAB_ACTIVE : TAB_IDLE)
            }
          >
            {item.label}
          </NavLink>
        ))}
      </div>
    </details>
  );
}

/**
 * Section tab strip. `items` may contain groups: a group renders as one
 * disclosure slot whose panel holds the child tabs, so a long section list
 * (Part detail has 17 routes) stays a compact single row instead of
 * scrolling six tabs off the right edge. The strip also wraps rather than
 * clipping, so no tab is ever unreachable at a narrow width.
 */
export default function SubNav({ items }: { items: SubNavEntry[] }) {
  return (
    <nav
      className="mb-4 inline-flex max-w-full flex-wrap items-center gap-0.5 rounded-md border border-border bg-panel2 p-1"
      aria-label="Section navigation"
    >
      {items.map(entry =>
        isGroup(entry) ? (
          <GroupTab key={`group:${entry.label}`} group={entry} />
        ) : (
          <Tab key={entry.to} item={entry} />
        ),
      )}
    </nav>
  );
}
