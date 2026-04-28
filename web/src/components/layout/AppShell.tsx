import { ReactNode, useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import {
  BarChart3,
  Boxes,
  ChevronDown,
  FolderKanban,
  Hammer,
  LogOut,
  Menu,
  Search,
  Settings,
  ShoppingCart,
  User,
  Warehouse,
  X,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import Brand from "@/components/Brand";
import ThemeToggle from "@/components/ThemeToggle";

type NavItem = { to: string; label: string; icon: typeof Boxes };

const NAV: NavItem[] = [
  { to: "/parts",    label: "Parts",    icon: Boxes },
  { to: "/storage",  label: "Storage",  icon: Warehouse },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/orders",   label: "Orders",   icon: ShoppingCart },
  { to: "/builds",   label: "Builds",   icon: Hammer },
  { to: "/reports",  label: "Reports",  icon: BarChart3 },
];

function pageTitleFor(pathname: string): string {
  // Strip leading slash, take the first segment, prettify.
  const seg = pathname.split("/").filter(Boolean)[0] ?? "";
  if (!seg) return "Home";
  if (seg === "settings") {
    const sub = pathname.split("/").filter(Boolean)[1] ?? "";
    return sub === "account" ? "Account" : "Workspace";
  }
  return seg.charAt(0).toUpperCase() + seg.slice(1);
}

export default function AppShell({ children }: { children: ReactNode }) {
  const { me, workspaceId, switchWorkspace, logout } = useAuth();
  const loc = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [userOpen, setUserOpen] = useState(false);

  // Close any drawer/menu on route change.
  useEffect(() => {
    setMobileOpen(false);
    setUserOpen(false);
  }, [loc.pathname]);

  return (
    <div className="min-h-full flex bg-bg text-text">
      <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />

      <div className="flex-1 min-w-0 flex flex-col">
        <header className="sticky top-0 z-20 border-b border-border bg-panel/80 backdrop-blur">
          <div className="px-4 h-12 flex items-center gap-3">
            <button
              type="button"
              onClick={() => setMobileOpen(true)}
              className="btn-ghost btn-sm lg:hidden"
              aria-label="Open menu"
            >
              <Menu size={16} />
            </button>

            <h1 className="text-sm font-semibold tracking-tight">
              {pageTitleFor(loc.pathname)}
            </h1>

            <div className="ml-auto flex items-center gap-2">
              <select
                className="input max-w-[200px]"
                value={workspaceId ?? ""}
                onChange={(e) => switchWorkspace(e.target.value)}
                aria-label="Switch workspace"
              >
                {me?.workspaces.map(w => (
                  <option key={w.id} value={w.id}>{w.name}</option>
                ))}
              </select>
              <ThemeToggle />
              <UserMenu
                open={userOpen}
                onToggle={() => setUserOpen(o => !o)}
                onLogout={logout}
                name={me?.user.name ?? ""}
              />
            </div>
          </div>
        </header>

        <main className="flex-1 px-4 py-4 min-w-0">{children}</main>
      </div>
    </div>
  );
}

function Sidebar({
  mobileOpen,
  onClose,
}: {
  mobileOpen: boolean;
  onClose: () => void;
}) {
  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 w-60 shrink-0 border-r border-border bg-panel flex flex-col",
          "transition-transform duration-150",
          "lg:sticky lg:top-0 lg:h-screen lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        )}
        aria-label="Primary navigation"
      >
        <div className="h-12 px-4 flex items-center justify-between border-b border-border">
          <Link to="/parts"><Brand /></Link>
          <button
            type="button"
            onClick={onClose}
            className="btn-ghost btn-sm lg:hidden"
            aria-label="Close menu"
          >
            <X size={16} />
          </button>
        </div>

        <SearchTrigger />

        <nav className="flex-1 px-2 pt-1 space-y-0.5 overflow-y-auto">
          {NAV.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 px-3 py-1.5 rounded-md text-sm transition-colors",
                  isActive
                    ? "bg-accent/15 text-accent"
                    : "text-muted hover:text-text hover:bg-panel2"
                )
              }
            >
              <item.icon size={16} />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="px-2 py-2 border-t border-border">
          <NavLink
            to="/settings/workspace"
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 px-3 py-1.5 rounded-md text-sm transition-colors",
                isActive
                  ? "bg-accent/15 text-accent"
                  : "text-muted hover:text-text hover:bg-panel2"
              )
            }
          >
            <Settings size={16} />
            Settings
          </NavLink>
        </div>
      </aside>
    </>
  );
}

function SearchTrigger() {
  // The actual command palette lands in step 4; this is the trigger surface.
  return (
    <div className="px-2 pt-2 pb-1">
      <button
        type="button"
        onClick={() =>
          window.dispatchEvent(new CustomEvent("stockmgr:openCommandPalette"))
        }
        className="w-full inline-flex items-center gap-2 rounded-md border border-border bg-bg px-3 py-1.5 text-left text-sm text-muted hover:bg-panel2 transition-colors"
      >
        <Search size={14} />
        <span className="flex-1 truncate">Search…</span>
        <span className="kbd">⌘K</span>
      </button>
    </div>
  );
}

function UserMenu({
  open,
  onToggle,
  onLogout,
  name,
}: {
  open: boolean;
  onToggle: () => void;
  onLogout: () => void;
  name: string;
}) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onToggle}
        className="btn-ghost btn-sm"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="hidden sm:inline max-w-[120px] truncate">{name}</span>
        <ChevronDown size={14} className={cn("transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-30 card p-1 min-w-[180px]"
          role="menu"
        >
          <Link
            to="/settings/account"
            className="flex items-center gap-2 px-3 py-1.5 rounded text-sm text-text hover:bg-panel2"
            role="menuitem"
          >
            <User size={14} />
            Account
          </Link>
          <button
            type="button"
            onClick={onLogout}
            className="w-full flex items-center gap-2 px-3 py-1.5 rounded text-sm text-text hover:bg-panel2"
            role="menuitem"
          >
            <LogOut size={14} />
            Log out
          </button>
        </div>
      )}
    </div>
  );
}
