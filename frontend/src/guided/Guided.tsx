import { useEffect, useState, type ReactNode } from "react";
import { ChartColumn, ChevronLeft, Cpu, Inbox, ListChecks, MessageCircle, History, PanelLeftClose, PanelLeftOpen, Scale, Settings, Users } from "lucide-react";
import { api, type Me } from "../api";
import { setReference } from "../format";
import { Chats } from "./Chats";
import { Ask, Decide } from "./Home";
import { ThemeToggle } from "./kit";
import { cameFrom } from "./trail";
import { LogicPage } from "./Logic";
import { NewRequest } from "./NewRequest";
import { RequestPage, type RequestStep } from "./Request";

export type GuidedRoute =
  | { page: "ask"; thread?: number } | { page: "chats" } | { page: "decide" } | { page: "new" } | { page: "request"; id: number; step: RequestStep }
  | { page: "logic"; id?: number };

/** The guided flow: drop in a request, see what is missing, one fee card, approve. Everything technical sits
 * behind "Details" (the older screens). */
export function parseGuided(hash: string): GuidedRoute | null {
  if (hash === "" || hash === "#" || hash === "#/") return { page: "ask" };
  if (hash === "#/decide") return { page: "decide" };
  if (hash === "#/chats") return { page: "chats" };
  const c = hash.match(/^#\/chat\/(\d+)$/);
  if (c) return { page: "ask", thread: Number(c[1]) };
  if (hash === "#/new") return { page: "new" };
  const l = hash.match(/^#\/logic(?:\/r\/(\d+))?$/);
  if (l) return { page: "logic", id: l[1] ? Number(l[1]) : undefined };
  const r = hash.match(/^#\/r\/(\d+)(?:\/(fee|proposal))?$/);
  if (r) return { page: "request", id: Number(r[1]), step: (r[2] as RequestStep | undefined) ?? "have" };
  return null;
}

type NavItem = { key: string; hash: string; label: string; icon: ReactNode; tab?: boolean; when?: (me: Me) => boolean };

const NAV: NavItem[] = [
  { key: "ask", hash: "#/", label: "Ask", icon: <MessageCircle className="bw-icon" />, tab: true },
  { key: "chats", hash: "#/chats", label: "Chats", icon: <History className="bw-icon" /> },
  { key: "decide", hash: "#/decide", label: "Decide", icon: <Inbox className="bw-icon" />, tab: true },
  { key: "requests", hash: "#/requests", label: "Requests", icon: <ListChecks className="bw-icon" />, tab: true },
  { key: "settings", hash: "#/settings", label: "Set up", icon: <Settings className="bw-icon" />, tab: true },
  { key: "logic", hash: "#/logic", label: "How we price", icon: <Scale className="bw-icon" /> },
  { key: "backtests", hash: "#/backtests", label: "Backtests", icon: <ChartColumn className="bw-icon" /> },
  { key: "members", hash: "#/members", label: "Members", icon: <Users className="bw-icon" /> },
  { key: "models", hash: "#/models", label: "Model calls", icon: <Cpu className="bw-icon" />,
    when: (me) => me.permissions.includes("backtest") },
];

/** The one shell every screen sits in: the rail on a desktop, the tab bar on a phone. */
export function Shell({ active, me, title, back: backTo, fallbackBack, onSignOut, onSwitch, children, wide, ownHeader }: {
  active: string; me: Me; title: string; back?: string; fallbackBack?: string; onSignOut: () => void; onSwitch: (orgId: string) => void;
  children: ReactNode; wide?: boolean; ownHeader?: boolean;
}) {
  const items = NAV.filter((n) => !n.when || n.when(me));
  const tabPage = items.some((n) => n.tab && n.key === active);
  const back = backTo ?? fallbackBack ?? (tabPage ? undefined : "#/");  // a page off the tabs always has a way back
  const [account, setAccount] = useState(false);
  const [collapsed, setCollapsed] = useRailCollapsed();
  return (
    <div className="bw-app" data-rail={collapsed ? "collapsed" : undefined}>
      <nav className="bw-rail" aria-label="Workspace">
        <div className="bw-rail__top">
          <a className="bw-brand" href="#/" aria-label="Bidwright, home">{collapsed ? "B" : "Bidwright"}</a>
          <button type="button" className="bw-icon-btn bw-rail__toggle" onClick={() => setCollapsed(!collapsed)}
                  aria-label={collapsed ? "Expand the menu" : "Collapse the menu"} aria-expanded={!collapsed}
                  title={collapsed ? "Expand the menu" : "Collapse the menu"}>
            {collapsed ? <PanelLeftOpen className="bw-icon" /> : <PanelLeftClose className="bw-icon" />}
          </button>
        </div>
        {items.map((n) => (
          <a key={n.key} className="bw-nav-link" href={n.hash} aria-current={n.key === active ? "page" : undefined}
             title={collapsed ? n.label : undefined} aria-label={collapsed ? n.label : undefined}>
            <span className="bw-row">{n.icon}<span className="bw-nav-link__text">{n.label}</span></span>
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
            {/* a tab page is a starting point: the brand, no back; anywhere else: back and the page's name */}
            {tabPage ? (
              <a className="bw-brand bw-mobile-only bw-header__brand" href="#/">Bidwright</a>
            ) : (
              <>
                {back && <a className="bw-icon-btn bw-back" href={back} aria-label="Back"><ChevronLeft className="bw-icon" /></a>}
                <div className="bw-header__text"><span className="bw-title-sm bw-mobile-only">{title}</span></div>
              </>
            )}
            <button type="button" className="bw-avatar bw-mobile-only bw-header__me" onClick={() => setAccount(true)}
                    aria-label={`${me.user.name}: account and more`} aria-haspopup="dialog">{initials(me.user.name)}</button>
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
  const title = { ask: "Ask", decide: "Decide", new: "Add files", request: "Request", logic: "How we price", chats: "Chats" }[route.page];
  const active = route.page === "request" ? "decide" : route.page === "new" ? "ask"
    : route.page === "ask" && route.thread ? "chats" : route.page;
  return (
    <Shell active={active} me={me} title={title} back={cameFrom() ?? undefined} fallbackBack={route.page === "ask" ? (route.thread ? "#/chats" : undefined) : route.page === "chats" ? "#/" : route.page === "decide" ? "#/"
             : route.page === "logic" ? (route.id ? `#/r/${route.id}/fee` : "#/settings") : "#/decide"}
           onSignOut={onSignOut} onSwitch={onSwitch} ownHeader={route.page === "request"}
           wide={route.page === "request" || route.page === "ask"}>
      {route.page === "ask" && <Ask threadId={route.thread} />}
      {route.page === "chats" && <Chats />}
      {route.page === "decide" && <Decide />}
      {route.page === "new" && <NewRequest />}
      {route.page === "logic" && <LogicPage key={route.id ?? 0} envelopeId={route.id} />}
      {route.page === "request" && <RequestPage key={route.id} id={route.id} step={route.step} />}
    </Shell>
  );
}

/** The desktop menu can fold down to its icons; the choice is remembered on this device. */
function useRailCollapsed(): [boolean, (v: boolean) => void] {
  const [collapsed, set] = useState(() => {
    try { return localStorage.getItem("bw-rail") === "collapsed"; } catch { return false; }
  });
  const update = (v: boolean) => {
    set(v);
    try { localStorage.setItem("bw-rail", v ? "collapsed" : "open"); } catch { /* not remembered */ }
  };
  return [collapsed, update];
}

const initials = (name: string) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
