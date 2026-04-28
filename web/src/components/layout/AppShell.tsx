import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Search } from "lucide-react";

const NAV = [
  { to: "/parts", label: "Parts" },
  { to: "/storage", label: "Storage" },
  { to: "/projects", label: "Projects" },
  { to: "/orders", label: "Orders" },
  { to: "/builds", label: "Builds" },
];

type SearchData = {
  parts: { id: string; name: string; mpn: string | null }[];
  storage_locations: { id: string; name: string }[];
  projects: { id: string; name: string }[];
  lots: { id: string; name: string | null; part_id: string }[];
  orders: { id: string; name: string; status: string }[];
};

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { me, workspaceId, switchWorkspace, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [q, setQ] = useState("");

  const { data: results } = useQuery({
    queryKey: ["search", q],
    queryFn: () => api.get<SearchData>(`/search?q=${encodeURIComponent(q)}`),
    enabled: q.trim().length >= 2,
  });

  return (
    <div className="min-h-full flex flex-col">
      <header className="border-b border-[#1f2229] bg-panel">
        <div className="px-4 h-12 flex items-center gap-6">
          <Link to="/parts" className="font-semibold text-accent">stockmgr</Link>
          <nav className="flex items-center gap-1">
            {NAV.map(n => (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  cn("px-3 py-1.5 rounded-md text-sm", isActive ? "bg-[#1c1f25] text-text" : "text-muted hover:text-text")
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="flex-1 max-w-xl relative">
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-2.5 text-muted" />
              <input
                className="input pl-8"
                placeholder="Search parts, storage, projects, lots…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
            {q.trim().length >= 2 && results && (
              <div className="absolute left-0 right-0 top-full mt-1 z-30 card max-h-96 overflow-auto">
                <SearchResults
                  results={results}
                  onPick={(href) => { setQ(""); nav(href); }}
                />
              </div>
            )}
          </div>
          <select
            className="input max-w-[180px]"
            value={workspaceId ?? ""}
            onChange={(e) => switchWorkspace(e.target.value)}
          >
            {me?.workspaces.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
          <Link to="/settings/account" className="text-muted text-sm hover:text-text">{me?.user.name}</Link>
          <button onClick={logout} className="btn">Logout</button>
        </div>
      </header>
      <main className="flex-1 px-4 py-4">{children}</main>
    </div>
  );
}

function SearchResults({ results, onPick }: { results: SearchData; onPick: (href: string) => void }) {
  const sections: [string, { id: string; label: string; href: string }[]][] = [
    ["Parts", results.parts.map(p => ({ id: p.id, label: p.name + (p.mpn ? ` — ${p.mpn}` : ""), href: `/parts/${p.id}/info` }))],
    ["Storage", results.storage_locations.map(s => ({ id: s.id, label: s.name, href: `/storage/${s.id}/info` }))],
    ["Projects", results.projects.map(p => ({ id: p.id, label: p.name, href: `/projects/${p.id}/data` }))],
    ["Lots", results.lots.map(l => ({ id: l.id, label: l.name || l.id, href: `/lots/${l.id}/info` }))],
    ["Orders", results.orders.map(o => ({ id: o.id, label: `${o.name} · ${o.status}`, href: `/orders/${o.id}` }))],
  ];
  return (
    <div className="p-2 text-sm">
      {sections.map(([title, items]) =>
        items.length ? (
          <div key={title} className="mb-2">
            <div className="text-xs uppercase tracking-wider text-muted px-2 mb-1">{title}</div>
            {items.slice(0, 8).map(it => (
              <button
                key={it.id}
                onClick={() => onPick(it.href)}
                className="w-full text-left px-2 py-1 rounded hover:bg-[#1c1f25]"
              >
                {it.label}
              </button>
            ))}
          </div>
        ) : null
      )}
    </div>
  );
}
