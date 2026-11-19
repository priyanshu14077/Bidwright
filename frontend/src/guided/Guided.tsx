import { useEffect, type ReactNode } from "react";
import { ChartColumn, ChevronLeft, Cpu, Inbox, ListChecks, MessageCircle, Settings, Users } from "lucide-react";
import { api, type Me } from "../api";
import { setReference } from "../format";
import { Ask, Decide } from "./Home";
import { ThemeToggle } from "./kit";
import { NewRequest } from "./NewRequest";
import { RequestPage, type RequestStep } from "./Request";

export type GuidedRoute =
  | { page: "ask" } | { page: "decide" } | { page: "new" } | { page: "request"; id: number; step: RequestStep };

/** The guided flow: drop in a request, see what is missing, one fee card, approve. Everything technical sits
 * behind "Details" (the older screens). */
export function parseGuided(hash: string): GuidedRoute | null {
  if (hash === "" || hash === "#" || hash === "#/") return { page: "ask" };
  if (hash === "#/decide") return { page: "decide" };
  if (hash === "#/new") return { page: "new" };
  const r = hash.match(/^#\/r\/(\d+)(?:\/(fee|proposal))?$/);
  if (r) return { page: "request", id: Number(r[1]), step: (r[2] as RequestStep | undefined) ?? "have" };
  return null;
}

type NavItem = { key: string; hash: string; label: string; icon: ReactNode; tab?: boolean; when?: (me: Me) => boolean };

const NAV: NavItem[] = [
  { key: "ask", hash: "#/", label: "Ask", icon: <MessageCircle className="bw-icon" />, tab: true },
  { key: "decide", hash: "#/decide", label: "Decide", icon: <Inbox className="bw-icon" />, tab: true },
  { key: "requests", hash: "#/requests", label: "Requests", icon: <ListChecks className="bw-icon" />, tab: true },
  { key: "settings", hash: "#/settings", label: "Set up", icon: <Settings className="bw-icon" />, tab: true },
  { key: "backtests", hash: "#/backtests", label: "Backtests", icon: <ChartColumn className="bw-icon" /> },
  { key: "members", hash: "#/members", label: "Members", icon: <Users className="bw-icon" /> },
  { key: "models", hash: "#/models", label: "Model calls", icon: <Cpu className="bw-icon" />,
    when: (me) => me.permissions.includes("backtest") },
];

/** The one shell every screen sits in: the rail on a desktop, the tab bar on a phone. */
export function Shell({ active, me, title, back, onSignOut, onSwitch, children, wide, ownHeader }: {
  active: string; me: Me; title: string; back?: string; onSignOut: () => void; onSwitch: (orgId: string) => void;
  children: ReactNode; wide?: boolean; ownHeader?: boolean;
}) {
  const items = NAV.filter((n) => !n.when || n.when(me));
  return (
    <div className="bw-app">
      <nav className="bw-rail" aria-label="Workspace">
        <a className="bw-brand" href="#/">Bidwright</a>
        {items.map((n) => (
          <a key={n.key} className="bw-nav-link" href={n.hash} aria-current={n.key === active ? "page" : undefined}>
            <span className="bw-row">{n.icon}{n.label}</span>
          </a>
        ))}
        <div className="bw-office">
          {me.workspaces.length > 1 ? (
            <select className="bw-input bw-office__select" aria-label="Workspace" value={me.workspace?.org_id}
                    onChange={(e) => onSwitch(e.target.value)}>
              {me.workspaces.map((w) => <option key={w.org_id} value={w.org_id}>{w.name}</option>)}
            </select>
          ) : <strong>{me.workspace?.name}</strong>}
          {me.workspace?.is_demo && <span className="bw-badge" data-status="estimated">Demo practice</span>}
          <span className="bw-muted">{me.user.name}, {me.role}</span>
          <ThemeToggle />
          <button type="button" className="bw-btn bw-btn--soft bw-btn--sm" onClick={onSignOut}>Sign out</button>
        </div>
      </nav>
      <main className="bw-main">
        {!ownHeader && (
          <header className="bw-header">
            {back && <a className="bw-icon-btn bw-back" href={back} aria-label="Back"><ChevronLeft className="bw-icon" /></a>}
            <div className="bw-header__text"><span className="bw-title-sm bw-mobile-only">{title}</span></div>
            <span className="bw-badge bw-mobile-only" title={me.user.email}>{initials(me.user.name)}</span>
          </header>
        )}
        <div className={`bw-main__inner${wide ? " bw-main__inner--wide" : ""}`}>{children}</div>
      </main>
      <nav className="bw-tabbar" aria-label="Workspace">
        {items.filter((n) => n.tab).map((n) => (
          <a key={n.key} href={n.hash} aria-current={n.key === active ? "page" : undefined}>{n.icon}{n.label}</a>
        ))}
      </nav>
    </div>
  );
}

export function Guided({ route, me, onSignOut, onSwitch }: {
  route: GuidedRoute; me: Me; onSignOut: () => void; onSwitch: (orgId: string) => void;
}) {
  useEffect(() => { api.reference().then(setReference, () => undefined); }, []);
  const title = { ask: "Ask", decide: "Decide", new: "Add files", request: "Request" }[route.page];
  const active = route.page === "request" ? "decide" : route.page === "new" ? "ask" : route.page;
  return (
    <Shell active={active} me={me} title={title} back={route.page === "ask" ? undefined : route.page === "decide" ? "#/" : "#/decide"}
           onSignOut={onSignOut} onSwitch={onSwitch} ownHeader={route.page === "request"}
           wide={route.page === "request"}>
      {route.page === "ask" && <Ask />}
      {route.page === "decide" && <Decide />}
      {route.page === "new" && <NewRequest />}
      {route.page === "request" && <RequestPage key={route.id} id={route.id} step={route.step} />}
    </Shell>
  );
}

const initials = (name: string) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
