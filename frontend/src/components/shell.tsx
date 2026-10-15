import { useQuery } from "@tanstack/react-query";
import { Bell, History, LogOut, MessagesSquare, Package, SlidersHorizontal } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { LiveState } from "../lib/live";
import type { Counts } from "../lib/types";

const NAV = [
  { to: "/leads", label: "Leads", icon: MessagesSquare, badge: "needs" as const },
  { to: "/alerts", label: "Alerts", icon: Bell, badge: "alerts" as const },
  { to: "/products", label: "Products", icon: Package },
  { to: "/settings", label: "Settings", icon: SlidersHorizontal },
  { to: "/activity", label: "Activity", icon: History },
];

const LIVE_TEXT: Record<LiveState, string> = {
  live: "Live",
  connecting: "Connecting",
  offline: "Offline - refreshing every 30 s",
};

function Badge({ n, hot = false }: { n: number | undefined; hot?: boolean }) {
  if (!n) return null;
  return (
    <span
      className="tabular ml-auto min-w-5 rounded-full px-1.5 text-center text-xs leading-5 font-semibold text-white"
      style={{ background: hot ? "var(--hot)" : "var(--ink-2)" }}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

export function Shell({ live }: { live: LiveState }) {
  const { me, signOut } = useAuth();
  const counts = useQuery({ queryKey: ["counts"], queryFn: () => api<Counts>("/api/leads/counts"), refetchInterval: 30000 });
  const unread = useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: () => api<{ unread: number }>("/api/notifications/unread-count"),
    refetchInterval: 30000,
  });
  const badges = { needs: counts.data?.needs_you, alerts: unread.data?.unread };

  const link = (to: string, label: string, Icon: typeof Bell, badge: ReactNode, compact: boolean) => (
    <NavLink
      key={to}
      to={to}
      className={({ isActive }) =>
        compact
          ? `relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px] font-medium ${isActive ? "text-ink" : "text-ink-3"}`
          : `flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium ${isActive ? "bg-sheet-2 text-ink" : "text-ink-2 hover:bg-sheet-2 hover:text-ink"}`
      }
    >
      <Icon className="size-[18px]" aria-hidden />
      {label}
      {compact ? <span className="absolute top-1 right-[calc(50%-1.4rem)]">{badge}</span> : badge}
    </NavLink>
  );

  return (
    <div className="flex h-full flex-col md:flex-row">
      <nav className="hidden w-56 shrink-0 flex-col border-r border-rule bg-sheet md:flex" aria-label="Main">
        <div className="flex items-center gap-2.5 px-5 pt-5 pb-6">
          <img src="/favicon.svg" alt="" className="size-7" />
          <div className="min-w-0">
            <p className="wide truncate text-[15px] font-extrabold">Lead desk</p>
            <p className="truncate text-xs text-ink-3">{me?.business_name || me?.email || " "}</p>
          </div>
        </div>
        <div className="flex flex-col gap-0.5 px-3">
          {NAV.map((n) => link(n.to, n.label, n.icon, n.badge ? <Badge n={badges[n.badge]} hot={n.badge === "needs"} /> : null, false))}
        </div>
        <div className="mt-auto flex flex-col gap-2 border-t border-rule px-5 py-4 text-xs text-ink-3">
          <span className="flex items-center gap-2" title="Live updates from the server">
            <span className="size-2 rounded-full" style={{ background: live === "live" ? "var(--ok)" : live === "connecting" ? "var(--warm)" : "var(--ink-3)" }} />
            {LIVE_TEXT[live]}
          </span>
          <button type="button" onClick={signOut} className="flex items-center gap-2 text-left hover:text-ink">
            <LogOut className="size-3.5" aria-hidden />
            Sign out
          </button>
        </div>
      </nav>

      <main className="min-h-0 min-w-0 flex-1 overflow-hidden">
        <Outlet />
      </main>

      <nav className="flex shrink-0 border-t border-rule bg-sheet md:hidden" aria-label="Main">
        {NAV.slice(0, 4).map((n) => link(n.to, n.label, n.icon, n.badge ? <Badge n={badges[n.badge]} hot={n.badge === "needs"} /> : null, true))}
      </nav>
    </div>
  );
}
