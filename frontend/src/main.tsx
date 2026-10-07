import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, type Reference } from "./api";
import { setReference } from "./format";
import { Inbox } from "./pages/Inbox";
import { Review } from "./pages/Review";
import { Accuracy, ModelLog, ReferenceData } from "./pages/Admin";
import "./styles.css";

type Route = { page: "inbox" } | { page: "review"; id: number } | { page: "reference" } | { page: "models" } | { page: "accuracy" };

function parse(hash: string): Route {
  const m = hash.match(/^#\/rfp\/(\d+)$/);
  if (m) return { page: "review", id: Number(m[1]) };
  if (hash === "#/reference") return { page: "reference" };
  if (hash === "#/models") return { page: "models" };
  if (hash === "#/accuracy") return { page: "accuracy" };
  return { page: "inbox" };
}

function App() {
  const [route, setRoute] = useState<Route>(parse(location.hash));
  const [reference, setRef] = useState<Reference | null>(null);
  const loadRef = useCallback(() => api.reference().then((r) => { setReference(r); setRef(r); }), []);
  useEffect(() => { loadRef(); }, [loadRef]);
  useEffect(() => {
    const on = () => setRoute(parse(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (hash: string) => { location.hash = hash; };
  const open = (id: number) => go(`#/rfp/${id}`);
  const nav: [Route["page"], string, string][] = [
    ["inbox", "#/", "Inbox"], ["accuracy", "#/accuracy", "Accuracy"], ["reference", "#/reference", "Reference data"],
    ["models", "#/models", "Model calls"],
  ];

  return (
    <div className="shell">
      <header className="bar">
        <a className="mark" href="#/">SOG <span>RFP intake</span></a>
        <nav>
          {nav.map(([page, hash, label]) => (
            <a key={page} href={hash} aria-current={route.page === page || (page === "inbox" && route.page === "review") ? "page" : undefined}>{label}</a>
          ))}
        </nav>
        <span className="env">Proof of concept, synthetic data</span>
      </header>
      <main>
        {route.page === "inbox" && <Inbox onOpen={open} />}
        {route.page === "review" && reference && <Review key={route.id} id={route.id} reference={reference} onBack={() => go("#/")} />}
        {route.page === "reference" && <ReferenceData reference={reference} onChanged={loadRef} />}
        {route.page === "models" && <ModelLog />}
        {route.page === "accuracy" && <Accuracy onOpen={open} />}
      </main>
    </div>
  );
}

const root = document.getElementById("root");
if (root) createRoot(root).render(<StrictMode><App /></StrictMode>);
