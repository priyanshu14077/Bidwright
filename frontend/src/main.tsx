import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { SIGNED_OUT, api, auth, type Me, type Reference } from "./api";
import { setReference } from "./format";
import { Session } from "./session";
import { Wordmark } from "./Wordmark";
import { Inbox } from "./pages/Inbox";
import { Review } from "./pages/Review";
import { Accuracy, ModelLog, ReferenceData } from "./pages/Admin";
import { Members } from "./pages/Members";
import { SignIn } from "./pages/SignIn";
import "./styles.css";

type Route =
  | { page: "inbox" } | { page: "review"; id: number } | { page: "reference" } | { page: "models" }
  | { page: "backtests" } | { page: "members" }
  | { page: "login" } | { page: "signup" } | { page: "demo" } | { page: "join"; token: string };

function parse(hash: string): Route {
  const rfp = hash.match(/^#\/rfp\/(\d+)$/);
  if (rfp) return { page: "review", id: Number(rfp[1]) };
  const join = hash.match(/^#\/join\/([\w-]+)$/);
  if (join) return { page: "join", token: join[1] };
  const simple = hash.replace(/^#\//, "");
  if (["reference", "models", "backtests", "members", "login", "signup", "demo"].includes(simple))
    return { page: simple } as Route;
  return { page: "inbox" };
}

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
    return <SignIn key={mode} mode={mode} invitation={route.page === "join" ? route.token : undefined}
                   onDone={() => { go("#/"); loadMe(); }} />;
  }
  if (!me.workspace) return <Session.Provider value={me}><NoWorkspace onDone={loadMe} /></Session.Provider>;

  const can = (p: string) => me.permissions.includes(p as never);
  const open = (id: number) => go(`#/rfp/${id}`);
  const nav: [Route["page"], string, string, boolean][] = [
    ["inbox", "#/", "Inbox", true],
    ["backtests", "#/backtests", "Backtests", true],
    ["reference", "#/reference", "Reference data", true],
    ["members", "#/members", "Members", true],
    ["models", "#/models", "Model calls", can("backtest")],
  ];
  const switchTo = async (orgId: string) => { await auth.switchTo(orgId); go("#/"); setRef(null); await loadMe(); };
  const signOut = async () => { await auth.logout(); setMe(null); go("#/login"); };
  const current = ["login", "signup", "demo", "join"].includes(route.page) ? "inbox" : route.page;

  return (
    <Session.Provider value={me}>
      <div className="shell">
        <header className="bar">
          <a href="#/" aria-label="Inbox"><Wordmark /></a>
          <nav aria-label="Workspace">
            {nav.filter((n) => n[3]).map(([page, hash, label]) => (
              <a key={page} href={hash} aria-current={current === page || (page === "inbox" && current === "review") ? "page" : undefined}>{label}</a>
            ))}
          </nav>
          <div className="bar-end">
            {me.workspace.is_demo && <span className="demo-tag">Demo practice</span>}
            <select className="workspace" aria-label="Workspace" value={me.workspace.org_id}
                    onChange={(e) => switchTo(e.target.value)}>
              {me.workspaces.map((w) => <option key={w.org_id} value={w.org_id}>{w.name}</option>)}
            </select>
            <span className="who" title={me.user.email}>{me.user.name}<span className="dim">, {me.role}</span></span>
            <button className="quiet" onClick={signOut}>Sign out</button>
          </div>
        </header>
        <main>
          {current === "inbox" && <Inbox onOpen={open} />}
          {route.page === "review" && reference && <Review key={route.id} id={route.id} reference={reference} onBack={() => go("#/")} />}
          {current === "reference" && <ReferenceData reference={reference} onChanged={loadRef} />}
          {current === "models" && <ModelLog />}
          {current === "backtests" && <Accuracy onOpen={open} />}
          {current === "members" && <Members />}
        </main>
      </div>
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

const root = document.getElementById("root");
if (root) createRoot(root).render(<StrictMode><App /></StrictMode>);
