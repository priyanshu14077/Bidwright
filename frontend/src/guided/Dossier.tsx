/* The request's dossier beside the conversation: how ready it is, every requirement with its value and source,
   the flags, the won-bid range and similar projects, and the one decision that comes next. It reads the same
   gate 1 assessment and fee card as the request's own screens, so the two never disagree. Decisions are made
   through the conversation (the assistant proposes, the person confirms), so they are recorded there. */
import { useEffect, useState, type CSSProperties } from "react";
import { ArrowRight, Check, Flag, X } from "lucide-react";
import { guided, type Evidence, type FeeCard, type Gate1, type GateField } from "../api";
import { fieldState, pillText } from "./fieldState";
import { RangeBar } from "./kit";
import { money, short } from "./ui";

const OPTIONS: Record<string, string[]> = {  // missing values that can be answered with a tap
  quality_tier: ["Mid", "High", "Luxury"],
  billing_currency: ["AED", "SAR", "QAR", "USD"],
};

type Props = {
  id: number; gate: Gate1; busy: boolean; refresh: number;
  /** Put a turn into the conversation (the assistant then proposes; the person confirms). */
  say: (text: string) => void;
  /** Open where a value came from: its pill in the conversation, or the source itself. */
  openSource: (pill: string, evidence: Evidence[]) => void;
  onClose?: () => void;
};

export function Dossier({ id, gate, busy, refresh, say, openSource, onClose }: Props) {
  const [fee, setFee] = useState<FeeCard | null>(null);
  useEffect(() => { guided.fee(id).then(setFee, () => setFee(null)); }, [id, refresh]);

  // the ring is the weighted completeness gate 1 decides on; the words count the requirements themselves
  const score = gate.completeness.score;
  const total = gate.fields.length, found = gate.fields.filter((f) => f.status === "found").length;
  const d = gate.decision?.decision;
  const feeDecided = fee?.decision && fee.decision.decision !== "reject";
  const status = d === "qualify" ? (feeDecided ? ["Fee approved", "found"] : ["Qualified", "found"])
    : d === "ask" ? ["Waiting on the client", "estimated"] : d === "decline" ? ["Declined", "missing"]
    : gate.recommendation === "go" ? ["Ready to price", "found"]
    : gate.recommendation === "clarify" ? ["Needs review", "estimated"] : ["Decline suggested", "missing"];
  const flags = [...gate.rules, ...gate.flags];
  const order = { missing: 0, ambiguous: 1, estimated: 2, found: 3 } as Record<string, number>;
  const fields = [...gate.fields].sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));

  return (
    <aside className="bw-dossier" aria-label="Request dossier">
      <header className="bw-dossier__head">
        <Ring score={score} />
        <div className="bw-dossier__title">
          <span className="bw-caps">Brief coverage</span>
          <strong>{found} of {total} requirements found</strong>
          <span className="bw-badge" data-status={status[1]}>{status[0]}</span>
        </div>
        {onClose && <button type="button" className="bw-icon-btn bw-mobile-only" aria-label="Close" onClick={onClose}><X className="bw-icon" /></button>}
      </header>

      <div className="bw-dossier__body">
        {flags.length > 0 && (
          <section className="bw-dossier__section" aria-label="Flags">
            <h3 className="bw-caps">Flags</h3>
            <ul className="bw-specs">
              {flags.map((r) => (
                <li key={r.name} className="bw-spec" data-state="flag">
                  <span className="bw-spec__icon" aria-label="Flag"><Flag aria-hidden /></span>
                  <span className="bw-spec__label">{r.message}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="bw-dossier__section" aria-label="Requirements">
          <h3 className="bw-caps">Requirements</h3>
          <ul className="bw-specs">
            {fields.map((f) => <Spec key={f.name} f={f} busy={busy} say={say} openSource={openSource} />)}
          </ul>
        </section>

        {fee?.status === "priced" && <Benchmark card={fee} />}
        {fee?.status === "missing" && (
          <section className="bw-dossier__section">
            <h3 className="bw-caps">Fee</h3>
            <p className="bw-muted">Can be priced once these are known: {fee.missing.map((m) => m.label).join(", ")}.</p>
          </section>
        )}
      </div>

      <footer className="bw-dossier__foot">
        <Primary gate={gate} fee={fee} id={id} busy={busy} say={say} />
        <a className="bw-btn bw-btn--outline bw-btn--block" href={d === "qualify" ? `#/r/${id}/fee` : `#/r/${id}`}>
          Open the request<ArrowRight className="bw-icon bw-icon--sm" />
        </a>
      </footer>
    </aside>
  );
}

/** The one decision that comes next. It goes into the conversation; the assistant prepares it for you to confirm. */
function Primary({ gate, fee, id, busy, say }: { gate: Gate1; fee: FeeCard | null; id: number; busy: boolean; say: (t: string) => void }) {
  const d = gate.decision?.decision;
  const feeDecided = fee?.decision && fee.decision.decision !== "reject";
  if (d === "qualify" && feeDecided) {
    return <a className="bw-btn bw-btn--primary bw-btn--block" href={`#/r/${id}/proposal`}>Open the proposal<ArrowRight className="bw-icon bw-icon--sm" /></a>;
  }
  if (d === "decline") return null;
  const [label, text] = d === "qualify" ? ["Approve the fee", "Approve the fee at the opening figure."]
    : d === "ask" ? ["Add the client's reply", ""]
    : gate.recommendation === "go" ? ["Qualify the request", "Qualify this request."]
    : gate.recommendation === "clarify" ? ["Ask the client", "Prepare the questions for the client and the decision to ask them."]
    : ["Decline the request", "Decline this request with the suggested reason."];
  return (
    <button type="button" className="bw-btn bw-btn--primary bw-btn--block" disabled={busy || (!text && d !== "ask")}
            onClick={() => (text ? say(text) : document.querySelector<HTMLTextAreaElement>(".bw-pi__text")?.focus())}>
      {label}<ArrowRight className="bw-icon bw-icon--sm" />
    </button>
  );
}

function Spec({ f, busy, say, openSource }: { f: GateField; busy: boolean; say: (t: string) => void;
  openSource: (pill: string, evidence: Evidence[]) => void }) {
  const st = fieldState(f);
  const [answer, setAnswer] = useState("");
  const missing = st.status === "missing";
  const send = (v: string) => { if (v.trim()) say(`The client confirmed: ${f.label} is ${v.trim()}. Please record it.`); setAnswer(""); };
  return (
    <li className="bw-spec" data-state={st.status}>
      <span className="bw-spec__icon" aria-label={missing ? "Missing" : st.status === "found" ? "Found" : "Estimated"}>
        {st.status === "found" ? <Check aria-hidden /> : missing ? null : "≈"}
      </span>
      <div className="bw-spec__main">
        <div className="bw-spec__row">
          <span className="bw-spec__label">{f.label}</span>
          {!missing && <span className="bw-spec__value">{st.value}</span>}
        </div>
        {st.source && (
          <button type="button" className="bw-spec__source" onClick={() => openSource(pillText(st.source!), st.evidence)}
                  title="Show where this came from">
            {pillText(st.source)}
          </button>
        )}
        {missing && (OPTIONS[f.name] ? (
          <div className="bw-spec__options" role="group" aria-label={`Set ${f.label}`}>
            {OPTIONS[f.name].map((o) => <button key={o} type="button" disabled={busy} onClick={() => send(o)}>{o}</button>)}
          </div>
        ) : (
          <form className="bw-spec__answer" onSubmit={(e) => { e.preventDefault(); send(answer); }}>
            <input className="bw-input" value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="The client's answer"
                   aria-label={`${f.label}: the client's answer`} disabled={busy} />
            <button type="submit" className="bw-btn bw-btn--soft bw-btn--sm" disabled={busy || !answer.trim()}>Add</button>
          </form>
        ))}
      </div>
    </li>
  );
}

function Benchmark({ card }: { card: Extract<FeeCard, { status: "priced" }> }) {
  const cur = card.currency;
  const agreed = card.won ? (card.won.low + card.won.high) / 2 : card.opening;
  const lo = card.won?.low ?? card.band.low, hi = card.won?.high ?? card.band.high;
  return (
    <section className="bw-dossier__section" aria-label="Benchmark">
      <h3 className="bw-caps">Fee against won bids</h3>
      <div className="bw-dossier__fee">
        <div><span className="bw-muted">Opening fee</span><strong>{money(card.opening, cur)}</strong></div>
        {card.won && <div><span className="bw-muted">Won bids landed</span><strong>{short(card.won.low, cur)}–{short(card.won.high, cur)}</strong></div>}
      </div>
      <RangeBar lo={lo} hi={hi} opening={card.opening} agreed={agreed}
                label={`Opening ${money(card.opening, cur)} against won bids ${money(lo, cur)} to ${money(hi, cur)}`}
                legend={[short(lo, cur), card.won ? `won bids ~${short(agreed, cur)}` : "range", short(hi, cur)]} />
      {card.won && <p className="bw-muted bw-dossier__note">{card.won.basis}.</p>}
      {card.comparables.items.length > 0 && (
        <details className="bw-details bw-dossier__comps">
          <summary>Similar projects ({card.comparables.items.length})</summary>
          <ul className="bw-comps">
            {card.comparables.items.map((c) => (
              <li key={c.reference}>
                <div className="bw-row"><strong>{c.title.split(" - ")[0]}</strong>
                  <span className="bw-push">{short(c.fee_in_target ?? c.fee_local, c.fee_in_target ? cur : c.currency)}</span></div>
                <span className="bw-muted">{c.city} · {c.year ?? ""} · {c.status}{c.size_m2 ? ` · ${c.size_m2.toLocaleString("en-US")} m²` : ""}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/** How much of what pricing needs is covered (gate 1's weighted completeness), as a ring. */
function Ring({ score }: { score: number }) {
  const p = Math.max(0, Math.min(1, score)), r = 22, c = 2 * Math.PI * r;
  return (
    <span className="bw-ring" role="img" aria-label={`${Math.round(p * 100)}% of the brief covered`}>
      <svg viewBox="0 0 52 52" aria-hidden>
        <circle cx="26" cy="26" r={r} className="bw-ring__track" />
        <circle cx="26" cy="26" r={r} className="bw-ring__fill" style={{ strokeDasharray: c, strokeDashoffset: c * (1 - p) } as CSSProperties} />
      </svg>
      <span>{Math.round(p * 100)}%</span>
    </span>
  );
}
