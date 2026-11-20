import { useEffect, useState } from "react";
import { ChevronRight, History, PanelRightClose, PanelRightOpen, Plus } from "lucide-react";
import { ChatPanel } from "./Chat";
import { renameCurrent } from "./trail";
import { api, type ChatThread, type Envelope } from "../api";
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
  if (e.lead_status === "recommend_no_bid") return "Suggested: decline. Confirm, or reply to the client";
  if (e.open_questions) return `${e.open_questions} question${e.open_questions === 1 ? "" : "s"} to ask the client`;
  return "Ready to price";
}

/** Working days from today to a date (negative when it has passed). */
function workingDaysTo(iso: string): number {
  const start = new Date(); start.setHours(0, 0, 0, 0);
  const end = new Date(iso); end.setHours(0, 0, 0, 0);
  const sign = end >= start ? 1 : -1;
  let n = 0;
  for (const d = new Date(start); sign > 0 ? d < end : d > end; d.setDate(d.getDate() + sign)) {
    const next = new Date(d); next.setDate(d.getDate() + sign);
    if (next.getDay() !== 0 && next.getDay() !== 6) n += sign;
  }
  return n;
}

/** What needs attention on a request: a deadline close or passed, or a client who has not answered. */
export function urgency(e: Envelope): { label: string; level: "now" | "soon" } | null {
  if (e.gate2_decision || e.gate1_decision === "decline") return null;
  if (e.submission_deadline) {
    const d = workingDaysTo(e.submission_deadline);
    if (d < 0) return { label: `Deadline passed ${-d} working day${d === -1 ? "" : "s"} ago`, level: "now" };
    if (d === 0) return { label: "Due today", level: "now" };
    if (d <= 3) return { label: `Due in ${d} working day${d === 1 ? "" : "s"}`, level: "now" };
    if (d <= 7) return { label: `Due in ${d} working days`, level: "soon" };
  }
  if (e.gate1_decision === "ask" && e.gate1_decided_at) {
    const days = Math.floor((Date.now() - new Date(e.gate1_decided_at).getTime()) / 86_400_000);
    if (days >= 2) return { label: `No reply from the client for ${days} days`, level: "soon" };
  }
  return null;
}
const rank = (e: Envelope) => { const u = urgency(e); return u ? (u.level === "now" ? 0 : 1) : 2; };

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

/** The first screen is the conversation: ask anything, or paste what a client sent. Below it, the ways into the
 * rest of the app, so a person can move on from wherever the conversation has got to. */
export function Ask({ threadId }: { threadId?: number }) {
  const { waiting } = useRequests();
  const [title, setTitle] = useState<string | null>(null);
  useEffect(() => { setTitle(null); }, [threadId]);
  // a new conversation gets its own address once it has one, so a refresh or a link returns to it
  const onThread = (t: ChatThread) => {
    setTitle(t.title);
    if (!threadId) { renameCurrent(`#/chat/${t.thread_id}`); location.replace(`#/chat/${t.thread_id}`); }
  };
  // two phases: before the first message, the box and its ways in sit in the middle of the screen; from the
  // first message, the whole screen is the conversation, with the box pinned at the bottom
  const [started, setStarted] = useState(!!threadId);
  // the request dossier beside the chat: there once the chat is about a request; open unless closed (remembered)
  const [hasDossier, setHasDossier] = useState(false);
  const [dossierOpen, setDossierOpen] = useState(() => {
    try { return localStorage.getItem("bw-dossier-open") !== "no"; } catch { return true; }
  });
  // a new chat (or another one) starts from its own phase: centred until its first message
  useEffect(() => { setStarted(!!threadId); setHasDossier(false); }, [threadId]);
  const toggleDossier = () => {
    const next = !dossierOpen;
    setDossierOpen(next);
    try { localStorage.setItem("bw-dossier-open", next ? "yes" : "no"); } catch { /* not remembered */ }
  };
  return (
    <div className="bw-ask" data-phase={started ? "chat" : "start"} data-dossier={hasDossier && dossierOpen ? "open" : undefined}>
      {started ? (
        <header className="bw-ask__bar">
          <h1 className="bw-title-sm bw-ask__title">{title ?? (threadId ? "Conversation" : "New conversation")}</h1>
          <div className="bw-row bw-nowrap">
            <a className="bw-btn bw-btn--outline bw-btn--sm bw-ask__btn" href="#/chats" aria-label="Past chats" title="Past chats">
              <History className="bw-icon bw-icon--sm" /><span>Past chats</span></a>
            <a className="bw-btn bw-btn--outline bw-btn--sm bw-ask__btn" href="#/" aria-label="New chat" title="New chat">
              <Plus className="bw-icon bw-icon--sm" /><span>New chat</span></a>
            {hasDossier && (
              <button type="button" className="bw-icon-btn bw-desktop-only bw-dossier-toggle" onClick={toggleDossier}
                      aria-pressed={dossierOpen} aria-label={dossierOpen ? "Hide the request dossier" : "Show the request dossier"}
                      title={dossierOpen ? "Hide the request dossier" : "Show the request dossier"}>
                {dossierOpen ? <PanelRightClose className="bw-icon" /> : <PanelRightOpen className="bw-icon" />}
              </button>
            )}
          </div>
        </header>
      ) : (
        <div className="bw-ask__intro">
          <h1 className="bw-display">Ask anything or paste a request</h1>
          <p className="bw-lead">Paste what the client sent or add their files, and I'll say what's missing. Ask why a fee is what it is, or what needs a decision.</p>
        </div>
      )}
      <ChatPanel key={threadId ?? "new"} threadId={threadId} onThread={onThread} autoFocus waiting={waiting.length}
                 onStarted={(x) => setStarted((was) => was || x)} withDossier dossierOpen={dossierOpen} onDossier={setHasDossier} />
    </div>
  );
}

/** Requests waiting on a decision. */
export function Decide() {
  const { items, waiting: unsorted, err } = useRequests();
  const finished = (items ?? []).filter(done).slice(0, 6);
  const can = useCan();
  // the most urgent first: a deadline close or passed, then a client who has gone quiet, then the rest
  const waiting = [...unsorted].sort((a, b) => rank(a) - rank(b));
  const urgent = waiting.map((e) => [e, urgency(e)] as const).filter(([, u]) => u?.level === "now");
  return (
    <>
      <div className="bw-stack" style={{ "--gap": "6px" } as React.CSSProperties}>
        <h1 className="bw-display">{items !== null && waiting.length === 0 ? "Nothing to decide" : "Decide"}</h1>
        <p className="bw-lead">{waiting.length ? `${waiting.length} request${waiting.length === 1 ? " is" : "s are"} waiting on you. ` : ""}Each one says what it needs next: ask the client, price it or approve it.</p>
      </div>
      {urgent.length > 0 && (
        <div className="bw-attention" role="status">
          <strong>{urgent.length === 1 ? "1 request needs attention" : `${urgent.length} requests need attention`}</strong>
          <span>{urgent.map(([e, u]) => `${e.project_name || e.title || `Request ${e.envelope_id}`}: ${u!.label.toLowerCase()}`).join(" · ")}</span>
        </div>
      )}
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
        <div className="bw-list-item__head">
          <span className="bw-list-item__title">{e.project_name || e.title || `Request ${e.envelope_id}`}</span>
          {outcome && <span className="bw-badge" data-status={outcome[1]}>{outcome[0]}</span>}
        </div>
        <div className="bw-muted">{[where, `received ${received}`].filter(Boolean).join(" · ")}</div>
        <div className="bw-body">{nextStep(e)}</div>
        {(() => { const u = urgency(e); return u && <span className="bw-due" data-level={u.level}>{u.label}</span>; })()}
      </div>
      <ChevronRight className="bw-icon" aria-hidden />
    </a>
  );
}
