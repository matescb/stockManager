import { NavLink } from "react-router-dom";
import { cn } from "@/lib/cn";

type Item = { to: string; label: string };

export default function SubNav({ items }: { items: Item[] }) {
  return (
    <nav
      className="mb-4 inline-flex max-w-full overflow-x-auto rounded-md border border-border bg-panel2 p-1"
      aria-label="Section navigation"
    >
      {items.map(i => (
        <NavLink
          key={i.to}
          to={i.to}
          end
          className={({ isActive }) =>
            cn(
              "px-3 py-1 rounded-[4px] text-sm whitespace-nowrap transition-colors",
              isActive
                ? "bg-panel text-text shadow-[0_1px_0_rgb(0_0_0_/_0.04)]"
                : "text-muted hover:text-text"
            )
          }
        >
          {i.label}
        </NavLink>
      ))}
    </nav>
  );
}
