/**
 * Shell for the in-app manual (`/help`, `/help/:slug`).
 *
 * A page rail on the left at `lg` and above, content on the right; below
 * `lg` the rail stacks above the page so no navigation is lost on a
 * phone in the warehouse. Every page is its own route, so a help page is
 * deep-linkable and back/forward works.
 */
import { NavLink, Outlet } from "react-router-dom";
import { BookOpen } from "lucide-react";
import { cn } from "@/lib/cn";
import { listDocs, HELP_BASE } from "@/lib/userDocs";

export default function HelpLayout() {
  const docs = listDocs();

  return (
    <div className="flex flex-col lg:flex-row gap-4 items-start">
      <nav
        className="card p-2 w-full lg:w-56 lg:shrink-0 lg:sticky lg:top-16"
        aria-label="Manual pages"
      >
        <div className="section-title px-2 pt-1 pb-2 flex items-center gap-1.5">
          <BookOpen size={12} />
          Manual
        </div>
        <NavLink to={HELP_BASE} end className={railClass}>
          Overview
        </NavLink>
        {docs.map(doc => (
          <NavLink key={doc.slug} to={`${HELP_BASE}/${doc.slug}`} className={railClass}>
            {doc.title || doc.slug}
          </NavLink>
        ))}
      </nav>

      <div className="flex-1 min-w-0">
        <Outlet />
      </div>
    </div>
  );
}

function railClass({ isActive }: { isActive: boolean }): string {
  return cn(
    "block px-2 py-1.5 rounded-md text-sm transition-colors",
    isActive ? "bg-accent/15 text-accent" : "text-muted hover:text-text hover:bg-panel2",
  );
}
