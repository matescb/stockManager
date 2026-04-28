import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Command } from "cmdk";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Boxes,
  FolderKanban,
  Hammer,
  Package,
  Settings,
  ShoppingCart,
  User,
  Warehouse,
} from "lucide-react";
import { api } from "@/lib/api";

type SearchData = {
  parts: { id: string; name: string; mpn: string | null }[];
  storage_locations: { id: string; name: string }[];
  projects: { id: string; name: string }[];
  lots: { id: string; name: string | null; part_id: string }[];
  orders: { id: string; name: string; status: string }[];
};

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(o => !o);
      }
    }
    function onOpen() { setOpen(true); }
    window.addEventListener("keydown", onKey);
    window.addEventListener("stockmgr:openCommandPalette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("stockmgr:openCommandPalette", onOpen);
    };
  }, []);

  const { data: results } = useQuery({
    queryKey: ["cp-search", q],
    queryFn: () => api.get<SearchData>(`/search?q=${encodeURIComponent(q)}`),
    enabled: open && q.trim().length >= 2,
    staleTime: 30_000,
  });

  function go(href: string) {
    setOpen(false);
    setQ("");
    navigate(href);
  }

  return (
    <Command.Dialog open={open} onOpenChange={setOpen} label="Command palette">
      <Command.Input
        value={q}
        onValueChange={setQ}
        placeholder="Type to search or jump…"
      />
      <Command.List>
        <Command.Empty>
          {q.trim().length >= 2 ? "No matches." : "Start typing to search…"}
        </Command.Empty>

        <Command.Group heading="Navigate">
          <Command.Item value="nav parts" onSelect={() => go("/parts")}>
            <Boxes size={14} /> Parts
          </Command.Item>
          <Command.Item value="nav storage" onSelect={() => go("/storage")}>
            <Warehouse size={14} /> Storage
          </Command.Item>
          <Command.Item value="nav projects" onSelect={() => go("/projects")}>
            <FolderKanban size={14} /> Projects
          </Command.Item>
          <Command.Item value="nav orders" onSelect={() => go("/orders")}>
            <ShoppingCart size={14} /> Orders
          </Command.Item>
          <Command.Item value="nav builds" onSelect={() => go("/builds")}>
            <Hammer size={14} /> Builds
          </Command.Item>
          <Command.Item value="nav reports" onSelect={() => go("/reports")}>
            <BarChart3 size={14} /> Reports
          </Command.Item>
          <Command.Item
            value="nav settings workspace members"
            onSelect={() => go("/settings/workspace")}
          >
            <Settings size={14} /> Workspace settings
          </Command.Item>
          <Command.Item
            value="nav settings account profile invitation"
            onSelect={() => go("/settings/account")}
          >
            <User size={14} /> Account
          </Command.Item>
        </Command.Group>

        {results?.parts && results.parts.length > 0 && (
          <Command.Group heading="Parts">
            {results.parts.map(p => (
              <Command.Item
                key={`p-${p.id}`}
                value={`part ${p.name} ${p.mpn ?? ""}`}
                onSelect={() => go(`/parts/${p.id}/info`)}
              >
                <Boxes size={14} />
                <span className="truncate">{p.name}</span>
                {p.mpn && <span className="text-muted text-xs ml-auto">{p.mpn}</span>}
              </Command.Item>
            ))}
          </Command.Group>
        )}

        {results?.storage_locations && results.storage_locations.length > 0 && (
          <Command.Group heading="Storage">
            {results.storage_locations.map(s => (
              <Command.Item
                key={`s-${s.id}`}
                value={`storage ${s.name}`}
                onSelect={() => go(`/storage/${s.id}/info`)}
              >
                <Warehouse size={14} /> {s.name}
              </Command.Item>
            ))}
          </Command.Group>
        )}

        {results?.projects && results.projects.length > 0 && (
          <Command.Group heading="Projects">
            {results.projects.map(p => (
              <Command.Item
                key={`pr-${p.id}`}
                value={`project ${p.name}`}
                onSelect={() => go(`/projects/${p.id}/data`)}
              >
                <FolderKanban size={14} /> {p.name}
              </Command.Item>
            ))}
          </Command.Group>
        )}

        {results?.orders && results.orders.length > 0 && (
          <Command.Group heading="Orders">
            {results.orders.map(o => (
              <Command.Item
                key={`o-${o.id}`}
                value={`order ${o.name} ${o.status}`}
                onSelect={() => go(`/orders/${o.id}`)}
              >
                <ShoppingCart size={14} />
                <span className="truncate">{o.name}</span>
                <span className="text-muted text-xs ml-auto">{o.status}</span>
              </Command.Item>
            ))}
          </Command.Group>
        )}

        {results?.lots && results.lots.length > 0 && (
          <Command.Group heading="Lots">
            {results.lots.map(l => (
              <Command.Item
                key={`l-${l.id}`}
                value={`lot ${l.name ?? l.id}`}
                onSelect={() => go(`/lots/${l.id}/info`)}
              >
                <Package size={14} /> {l.name || l.id}
              </Command.Item>
            ))}
          </Command.Group>
        )}
      </Command.List>
    </Command.Dialog>
  );
}
