/* Typed building blocks for the guided screens. They carry the design rules, so screens cannot break them:
   a source pill only accepts a structured source, a field shows exactly one source or a question, a flag
   only says what to check, and a second hero card on a view warns in development. */
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Check, Flag as FlagIcon, Info } from "lucide-react";
import type { Confidence, Evidence } from "../api";
import { pillText, type SourceSpec, type UiStatus } from "./fieldState";
import { EvidenceDetail } from "./ui";

export const isDesktop = () => typeof window !== "undefined" && window.matchMedia("(min-width: 1024px)").matches;

export function useDesktop(): boolean {
  const [desk, setDesk] = useState(isDesktop());
  useEffect(() => {
    const m = window.matchMedia("(min-width: 1024px)");
    const on = () => setDesk(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return desk;
}

/** "Where did this come from". The text is always built from the source, never passed in. */
export function SourcePill({ source, onOpen }: { source: SourceSpec; onOpen: () => void }) {
  return (
    <button type="button" className="bw-source" data-kind={source.kind} onClick={onOpen}>
      <span className="bw-source__dot" aria-hidden />
      <span className="bw-source__text">{pillText(source)}</span>
    </button>
  );
}

export function StatusDisc({ status }: { status: UiStatus }) {
  const word = { found: "Found", estimated: "Estimated", missing: "Missing" }[status];
  return (
    <span className="bw-status" data-status={status} role="img" aria-label={word}>
      {status === "found" ? <Check className="bw-icon" aria-hidden /> : status === "missing" ? "?" : "≈"}
    </span>
  );
}

export function Badge({ status, children, changed }: { status?: UiStatus; children: ReactNode; changed?: boolean }) {
  return <span className="bw-badge" data-status={status} data-changed={changed || undefined}>{children}</span>;
}

export function ConfidenceMeter({ level }: { level: Confidence }) {
  return <span className="bw-confidence" data-level={level} aria-hidden><i /><i /><i /></span>;
}

/** One value: its status, the value, one source (or the question to ask), and on desktop the card turns over
 * to show the source. On a phone the source opens in a sheet (onSheet). */
export function Field({ label, status, value, range, source, evidence, question, note, confidence, changed, arriving, i,
                        onSheet, envelopeId }: {
  label: string; status: UiStatus; value: string; range?: string | null; source: SourceSpec | null; evidence: Evidence[];
  question?: string; note?: string | null; confidence?: Confidence | null; changed?: boolean; arriving?: boolean; i?: number;
  onSheet: (evidence: Evidence[], label: string) => void; envelopeId?: number;
}) {
  const [flipped, setFlipped] = useState(false);
  const open = () => (isDesktop() ? setFlipped(true) : onSheet(evidence, label));
  const front = (
    <div className="bw-field__body">
      <span className="bw-field__label">{label}</span>
      {status !== "missing" && value && (
        <span className="bw-field__value">{value}{range && <span className="bw-muted"> ({range})</span>}</span>
      )}
      {status === "estimated" && confidence && source?.kind === "estimated" && (
        <span className="bw-muted"><ConfidenceMeter level={confidence} /> {confidence} confidence</span>
      )}
      {note && <span className="bw-muted">{note}</span>}
      {question && <p className="bw-question">{question}</p>}
      {source && <SourcePill source={source} onOpen={open} />}
    </div>
  );
  return (
    <div className="bw-field" data-changed={changed || undefined} data-arriving={arriving || undefined}
         style={{ "--i": i ?? 0 } as CSSProperties}>
      <StatusDisc status={status} />
      {source ? (
        <div className="bw-flip" data-flipped={flipped || undefined}>
          <div className="bw-flip__face">{front}</div>
          <div className="bw-flip__face bw-flip__back" aria-hidden={!flipped}>
            {flipped && (  /* the back exists only while turned over, so it never sets the card's height */
              <div className="bw-field__body">
                <span className="bw-field__label">{label}: where this came from</span>
                {evidence.slice(0, 2).map((e, k) => <EvidenceDetail key={k} e={e} envelopeId={envelopeId} />)}
                <button type="button" className="bw-btn bw-btn--soft bw-btn--sm" onClick={() => setFlipped(false)}>Back to the value</button>
              </div>
            )}
          </div>
        </div>
      ) : front}
    </div>
  );
}

/** Something to check before the fee goes out. Only the fee card uses flags; facts are fields. */
export function Flag({ level, children, source }: { level: "check" | "info"; children: ReactNode; source?: ReactNode }) {
  return (
    <div className="bw-flag" data-level={level}>
      <span className="bw-status" data-status={level === "check" ? "estimated" : undefined}>
        {level === "check" ? <FlagIcon className="bw-icon bw-icon--sm" aria-hidden /> : <Info className="bw-icon bw-icon--sm" aria-hidden />}
      </span>
      <div className="bw-flag__text"><span>{children}</span>{source}</div>
    </div>
  );
}

let heroes = 0;

/** The one card that matters on a view. */
export function HeroCard({ children, label, className = "", i }: { children: ReactNode; label: string; className?: string; i?: number }) {
  useEffect(() => {
    heroes += 1;
    if (import.meta.env.DEV && heroes > 1) console.warn(`[design] ${heroes} hero cards are mounted; a view has one.`);
    return () => { heroes -= 1; };
  }, []);
  return <section className={`bw-card bw-card--hero ${className}`} aria-label={label}
                  style={i === undefined ? undefined : { "--i": i } as CSSProperties}>{children}</section>;
}

export function PinnedCard({ title, children, i, className = "" }: { title: string; children: ReactNode; i?: number; className?: string }) {
  return (
    <section className={`bw-card ${className}`} aria-label={title} style={{ "--i": i ?? 0 } as CSSProperties}>
      <h2 className="bw-caps">{title}</h2>
      {children}
    </section>
  );
}

export function rangeVars(o: { lo: number; hi: number; opening: number; agreed: number }) {
  const min = Math.min(o.lo, o.agreed) * 0.95, max = Math.max(o.hi, o.opening) * 1.03;
  const pct = (v: number) => `${(((v - min) / (max - min)) * 100).toFixed(1)}%`;
  return { "--lo": pct(o.lo), "--hi": pct(o.hi), "--open": pct(o.opening), "--agreed": pct(o.agreed) } as CSSProperties;
}

export function RangeBar({ lo, hi, opening, agreed, label, legend }: {
  lo: number; hi: number; opening: number; agreed: number; label: string; legend: [string, string, string];
}) {
  return (
    <div>
      <div className="bw-range" style={rangeVars({ lo, hi, opening, agreed })} role="img" aria-label={label}>
        <div className="bw-range__band" />
        <div className="bw-range__marker" data-kind="agreed" />
        <div className="bw-range__marker" data-kind="opening" />
      </div>
      <div className="bw-range-legend"><span>{legend[0]}</span><span>{legend[1]}</span><span>{legend[2]}</span></div>
    </div>
  );
}

export function ActionBar({ children }: { children: ReactNode }) {
  return <div className="bw-actionbar">{children}</div>;
}

/** Any change proposed from chat: old value struck through, new value, reason; nothing happens until confirmed. */
export function ConfirmCard({ status, before, after, summary, reason, primary, onConfirm, onCancel }: {
  status: "pending" | "confirmed" | "cancelled" | "failed"; before?: string; after?: string; summary: string;
  reason?: string; primary: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <div className="bw-confirm" data-status={status}>
      <span className="bw-caps">{{ pending: "Needs your confirmation", confirmed: "Confirmed", cancelled: "Cancelled", failed: "Could not be done" }[status]}</span>
      {before && after ? (
        <div className="bw-confirm__change"><span className="bw-confirm__was">{before}</span><strong>{after}</strong></div>
      ) : <strong className="bw-body">{summary}</strong>}
      {reason && <span className="bw-muted">Reason: {reason}</span>}
      {status === "pending" && (
        <div className="bw-confirm__actions">
          <button type="button" className={`bw-btn bw-btn--sm ${primary ? "bw-btn--primary" : "bw-btn--soft"}`} onClick={onConfirm}>Confirm</button>
          <button type="button" className="bw-btn bw-btn--outline bw-btn--sm" onClick={onCancel}>Cancel</button>
        </div>
      )}
    </div>
  );
}

/** Dark is opt-in: a person's choice, remembered on this device. Light is the product. */
export function applyTheme() {
  try {
    if (localStorage.getItem("bw-theme") === "dark") document.documentElement.dataset.theme = "dark";
    else delete document.documentElement.dataset.theme;
  } catch { /* storage blocked: stay light */ }
}

export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark");
  const toggle = () => {
    const next = !dark;
    try { localStorage.setItem("bw-theme", next ? "dark" : "light"); } catch { /* not remembered */ }
    setDark(next);
    applyTheme();
  };
  return (
    <button type="button" className="bw-btn bw-btn--soft bw-btn--sm" aria-pressed={dark} onClick={toggle}>
      {dark ? "Use light theme" : "Use dark theme"}
    </button>
  );
}
