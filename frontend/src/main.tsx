import "./styles.css";
import "./bw-ui.css";
import "./guided/guided.css";
import "./guided/legacy.css";
import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { SIGNED_OUT, api, auth, type Me, type Reference } from "./api";
import { setReference } from "./format";
import { Session } from "./session";
import { Wordmark } from "./Wordmark";
import { Inbox } from "./pages/Inbox";
import { Review } from "./pages/Review";
import { Accuracy, ModelLog } from "./pages/Admin";
import { Members } from "./pages/Members";
import { ProposalPage } from "./pages/Proposal";
import { Settings, type SettingsTab } from "./pages/Settings";
import { SignIn } from "./pages/SignIn";
import { Guided, Shell, parseGuided, type GuidedRoute } from "./guided/Guided";
import { applyTheme } from "./guided/kit";

type Route =
  | { page: "guided"; route: GuidedRoute } | { page: "inbox" } | { page: "review"; id: number } | { page: "proposal"; id: number; version: number }
  | { page: "settings"; tab: SettingsTab } | { page: "models" }
  | { page: "backtests" } | { page: "members" }
  | { page: "login" } | { page: "signup" } | { page: "demo" } | { page: "join"; token: string };

function parse(hash: string): Route {
  const g = parseGuided(hash);
  if (g) return { page: "guided", route: g };
  if (hash === "#/requests") return { page: "inbox" };
  const rfp = hash.match(/^#\/rfp\/(\d+)$/);
  if (rfp) return { page: "review", id: Number(rfp[1]) };
  const proposal = hash.match(/^#\/proposal\/(\d+)\/v\/(\d+)$/);
  if (proposal) return { page: "proposal", id: Number(proposal[1]), version: Number(proposal[2]) };
  if (hash === "#/reference" || hash.startsWith("#/settings")) {
    const name = hash.split("/")[2];
    return { page: "settings", tab: SETTINGS_TABS.find((t) => t === name) ?? "vocabulary" };
  }
  const join = hash.match(/^#\/join\/([\w-]+)$/);
  if (join) return { page: "join", token: join[1] };
  const simple = hash.replace(/^#\//, "");
  if (["models", "backtests", "members", "login", "signup", "demo"].includes(simple))
    return { page: simple } as Route;
  return { page: "inbox" };
}

const SETTINGS_TABS: SettingsTab[] = ["vocabulary", "pricing", "library", "studios"];
const go = (hash: string) => { location.hash = hash; };

function App() {
  const [route, setRoute] = useState<Route>(parse(location.hash));
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [reference, setRef] = useState<Reference | null>(null);

  const loadMe = useCallback(() => auth.me().then(setMe, () => setMe(null)), []);
  const loadRef = useCallback(() => api.reference().then((r) => { setReference(r); setRef(r); }), []);
  useEffect(() => { loadMe(); }, [loadMe]);
  useEffect(() => { if (me?.workspace) loadRef(); }, [me?.workspace, loadRef]);
  useEffect(() => {
    const onHash = () => setRoute(parse(location.hash));
    const onSignedOut = () => setMe(null);
    window.addEventListener("hashchange", onHash);
    window.addEventListener(SIGNED_OUT, onSignedOut);
    return () => { window.removeEventListener("hashchange", onHash); window.removeEventListener(SIGNED_OUT, onSignedOut); };
  }, []);
  // An invitation link opened while signed in joins straight away.
  useEffect(() => {
    if (me && route.page === "join") auth.join(route.token).then(() => { go("#/"); loadMe(); }, () => go("#/"));
  }, [me, route, loadMe]);

  if (me === undefined) return null;
  if (me === null) {
    const mode = route.page === "signup" || route.page === "demo" || route.page === "join" ? route.page : "login";
    return (
      <div className="bw-app bw-legacy bw-gate-wrap">
        <SignIn key={mode} mode={mode} invitation={route.page === "join" ? route.token : undefined}
                onDone={() => { go("#/"); loadMe(); }} />
      </div>
    );
  }
  if (!me.workspace) return <Session.Provider value={me}><div className="bw-app bw-legacy bw-gate-wrap"><NoWorkspace onDone={loadMe} /></div></Session.Provider>;

  const signOut = async () => { await auth.logout(); setMe(null); go("#/login"); };
  const switchTo = async (orgId: string) => { await auth.switchTo(orgId); go("#/"); setRef(null); await loadMe(); };
  if (route.page === "guided") {
    return <Session.Provider value={me}><Guided route={route.route} me={me} onSignOut={signOut} onSwitch={switchTo} /></Session.Provider>;
  }
  const open = (id: number) => go(`#/r/${id}`);
  const current = ["login", "signup", "demo", "join"].includes(route.page) ? "inbox" : route.page;

  const titles: Record<string, string> = { inbox: "Requests", review: "Request details", proposal: "Proposal",
    settings: "Set up", models: "Model calls", backtests: "Backtests", members: "Members" };
  const active = current === "inbox" || current === "review" ? "requests" : current === "proposal" ? "decide" : current;
  const wide = current === "review" || current === "proposal" || current === "backtests";
  return (
    <Session.Provider value={me}>
      <Shell active={active} me={me} title={titles[current] ?? "Bidwright"} onSignOut={signOut} onSwitch={switchTo} wide={wide}
             back={current === "review" ? `#/r/${(route as { id: number }).id}` : current === "proposal" ? "#/decide" : undefined}>
        <div className="bw-legacy">
          {current === "inbox" && <Inbox onOpen={open} />}
          {route.page === "review" && reference && <Review key={route.id} id={route.id} reference={reference} onBack={() => go(`#/r/${route.id}`)} />}
          {route.page === "proposal" && <ProposalPage key={`${route.id}-${route.version}`} proposalId={route.id}
                                                      versionNo={route.version} reference={reference} />}
          {route.page === "settings" && <Settings tab={route.tab} reference={reference} onChanged={loadRef} />}
          {current === "models" && <ModelLog />}
          {current === "backtests" && <Accuracy onOpen={open} />}
          {current === "members" && <Members />}
        </div>
      </Shell>
    </Session.Provider>
  );
}

/** Signed in, but no longer a member of any workspace. */
function NoWorkspace({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const create = async () => {
    try { await auth.createWorkspace(name); onDone(); } catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
  };
  return (
    <div className="gate">
      <span className="gate-mark"><Wordmark /></span>
      <div className="gate-card">
        <h1>You are not in a workspace</h1>
        <p className="dim">Ask a colleague for an invitation link, or start a workspace for your practice.</p>
        <label>Practice name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        {err && <p className="error" role="alert">{err}</p>}
        <button className="primary wide" disabled={name.trim().length < 2} onClick={create}>Create workspace</button>
        <button className="quiet" onClick={() => auth.logout().then(() => location.reload())}>Sign out</button>
      </div>
    </div>
  );
}

applyTheme();  // light unless this person chose dark
const root = document.getElementById("root");
if (root) createRoot(root).render(<StrictMode><App /></StrictMode>);
