import { ReactNode } from "react";
import { NavLink } from "react-router-dom";

const ITEMS = [
  { to: "/parts",               label: "Parts",         end: true },
  { to: "/parts/lots",          label: "Lots" },
  { to: "/parts/stock/history", label: "Stock history" },
  { to: "/parts/archived",      label: "Archived" },
];

/**
 * Top strip shown on every page that's part of the "Parts" area:
 * the parts list, the archived list, the all-lots list, and the
 * global stock history.
 */
export default function PartsTopNav({ rightAccessory }: { rightAccessory?: ReactNode }) {
  return (
    <div className="flex items-center gap-1 mb-3">
      {ITEMS.map(it => (
        <NavLink
          key={it.to}
          to={it.to}
          end={it.end}
          className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}
        >
          {it.label}
        </NavLink>
      ))}
      {rightAccessory && <div className="ml-auto flex gap-1">{rightAccessory}</div>}
    </div>
  );
}
