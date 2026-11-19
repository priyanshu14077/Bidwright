import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { ChatPanel } from "./Chat";
import { api, type Envelope } from "../api";
import { useCan } from "../session";
import { errorText, money } from "./ui";

const OUTCOME: Record<string, [string, "found" | "estimated" | "missing"]> = {
  qualified: ["Go", "found"], needs_info: ["Clarify", "estimated"], recommend_no_bid: ["No-go", "missing"],
};

function nextStep(e: Envelope): string {
  if (["received", "reading", "extracting"].includes(e.status)) return "Reading the request…";
  if (e.status === "failed") return "Could not be read; open it to try again";
  if (e.gate2_decision === "reject") return "Fee rejected";
  if (e.gate2_decision) return e.proposal_total ? `Approved at ${money(e.proposal_total, e.proposal_currency ?? "")}` : "Fee approved";
  if (e.gate1_decision === "decline") return "Declined";
  if (e.gate1_decision === "qualify") return "Qualified: the fee is waiting for approval";
  if (e.gate1_decision === "ask") return "Waiting for the client's answers";
  if (e.open_questions) return `${e.open_questions} question${e.open_questions === 1 ? "" : "s"} to ask the client`;
  return "Ready to price";
}

const done = (e: Envelope) => e.gate1_decision === "decline" || !!e.gate2_decision;

function useRequests() {
  const [items, setItems] = useState<Envelope[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const load = () => api.inbox().then(setItems, (e) => setErr(errorText(e)));
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);
  const waiting = (items ?? []).filter((e) => !done(e) && !(e.title ?? "").startsWith("[gold"));
  return { items, waiting, err };
}

/** The first screen: ask anything, or paste what a client sent. What is waiting sits underneath. */
export function Ask() {
  const { waiting } = useRequests();
  return (
    <>
      <div className="bw-stack" style={{ "--gap": "6px" } as React.CSSProperties}>
        <h1 className="bw-display">Ask anything or paste a request</h1>
        <p className="bw-lead">Paste a WhatsApp message, an email or your call notes and I'll tell you what's missing. Ask why a fee is what it is, or what needs a decision.</p>
      </div>
      <section className="bw-card bw-card--hero bw-chat-home" aria-label="Chat"><ChatPanel autoFocus /></section>
      {waiting.length > 0 && (
        <section className="bw-stack bw-enter" aria-label="Waiting on you">
          <div className="bw-row">
            <h2 className="bw-caps">Decide on {waiting.length} request{waiting.length === 1 ? "" : "s"}</h2>
            <a className="bw-push bw-muted" href="#/decide">See all</a>
          </div>
          {waiting.slice(0, 3).map((e) => <Row key={e.envelope_id} e={e} />)}
        </section>
      )}
    </>
  );
}

/** Requests waiting on a decision. */
export function Decide() {
  const { items, waiting, err } = useRequests();
  const finished = (items ?? []).filter(done).slice(0, 6);
  const can = useCan();
  return (
    <>
      <div className="bw-stack" style={{ "--gap": "6px" } as React.CSSProperties}>
        <h1 className="bw-display">{items !== null && waiting.length === 0 ? "Nothing to decide" : "Decide"}</h1>
        <p className="bw-lead">{waiting.length ? `${waiting.length} request${waiting.length === 1 ? " is" : "s are"} waiting on you. ` : ""}Each one says what it needs next: ask the client, price it or approve it.</p>
      </div>
      {can("intake") && <a className="bw-btn bw-btn--soft bw-home-new" href="#/">Add a request in Ask</a>}
      {err && <p role="alert" className="bw-body">{err}</p>}
      {items === null ? <p className="bw-muted">Loading…</p> : (
        <section className="bw-stack bw-enter" aria-label="Waiting on you">
          {waiting.length === 0 && <p className="bw-muted">Paste a client's message in Ask to start one.</p>}
          {waiting.map((e, i) => <Row key={e.envelope_id} e={e} i={i} />)}
        </section>
      )}
      {finished.length > 0 && (
        <section className="bw-stack" aria-label="Recently decided">
          <h2 className="bw-caps">Recently decided</h2>
          {finished.map((e) => <Row key={e.envelope_id} e={e} />)}
        </section>
      )}
      <p className="bw-muted">Formal RFP packs and every request are under <a href="#/requests">Requests</a>.</p>
    </>
  );
}

function Row({ e, i = 0 }: { e: Envelope; i?: number }) {
  const outcome = e.lead_status ? OUTCOME[e.lead_status] : null;
  const where = [e.city, e.country].filter(Boolean).join(", ");
  const received = new Date(e.received_at).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  return (
    <a className="bw-list-item" href={`#/r/${e.envelope_id}`} style={{ "--i": i } as React.CSSProperties}>
      <div className="bw-list-item__main">
        <div className="bw-row" style={{ "--gap": "8px" } as React.CSSProperties}>
          <span className="bw-list-item__title">{e.project_name || e.title || `Request ${e.envelope_id}`}</span>
          {outcome && <span className="bw-badge" data-status={outcome[1]}>{outcome[0]}</span>}
        </div>
        <div className="bw-muted">{[where, `received ${received}`].filter(Boolean).join(" · ")}</div>
        <div className="bw-body">{nextStep(e)}</div>
      </div>
      <ChevronRight className="bw-icon" aria-hidden />
    </a>
  );
}
