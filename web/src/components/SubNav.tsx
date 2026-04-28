import { NavLink } from "react-router-dom";
import { cn } from "@/lib/cn";

type Item = { to: string; label: string };

export default function SubNav({ items }: { items: Item[] }) {
  return (
    <nav className="card p-2 mb-4 flex flex-wrap gap-1">
      {items.map(i => (
        <NavLink
          key={i.to}
          to={i.to}
          end
          className={({ isActive }) =>
            cn(
              "px-3 py-1 rounded-md text-sm",
              isActive ? "bg-panel2 text-text" : "text-muted hover:text-text"
            )
          }
        >
          {i.label}
        </NavLink>
      ))}
    </nav>
  );
}
