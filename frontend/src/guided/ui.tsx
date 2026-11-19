import { useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { guided, type Evidence } from "../api";

/** Fees always carry their currency code; INR uses Indian digit grouping. */
export const money = (n: number, currency: string) =>
  `${currency} ${new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", { maximumFractionDigits: 0 }).format(n)}`;

export const short = (n: number, currency: string) => {
  const a = Math.abs(n);
  return `${currency} ${a >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : a >= 1e3 ? `${Math.round(n / 1e3)}k` : Math.round(n)}`;
};

export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

const CHANNEL: Record<string, string> = { whatsapp: "WhatsApp", email: "Email", notes: "Call notes", upload: "Upload" };

/** A native dialog: a bottom sheet on a phone, a centred dialog on a desktop. */
export function Sheet({ open, title, onClose, children }:
  { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="bw-sheet" onClose={onClose} onClick={(e) => { if (e.target === ref.current) onClose(); }}
            aria-label={title}>
      <div className="bw-sheet__grab" aria-hidden />
      <div className="bw-sheet__head">
        <h2 className="bw-title-sm">{title}</h2>
        <button type="button" className="bw-icon-btn bw-push" aria-label="Close" onClick={onClose}><X className="bw-icon" /></button>
      </div>
      <div className="bw-sheet__body">{open && children}</div>
    </dialog>
  );
}

/** The original message with the quoted words highlighted, read from the document's stored text. */
function QuotedText({ e }: { e: Evidence }) {
  const [body, setBody] = useState<string | null>(null);
  useEffect(() => {
    if (e.document_id && e.has_text) guided.documentText(e.document_id).then(setBody, () => setBody(null));
  }, [e.document_id, e.has_text]);
  if (!body) return e.quote ? <p className="bw-quote">“<mark>{e.quote}</mark>”</p> : null;
  let [start, end] = e.span ?? [-1, -1];
  if (start < 0 && e.quote) {
    start = body.indexOf(e.quote);
    end = start + e.quote.length;
  }
  if (start < 0) return <p className="bw-quote bw-message-preview">{body.slice(0, 600)}</p>;
  const from = Math.max(0, body.lastIndexOf("\n", Math.max(0, start - 160)));
  const to = Math.min(body.length, body.indexOf("\n", end + 160) === -1 ? body.length : body.indexOf("\n", end + 160));
  return (
    <p className="bw-quote bw-message-preview">
      {from > 0 && "… "}{body.slice(from, start).trimStart()}
      <mark>{body.slice(start, end)}</mark>
      {body.slice(end, to)}{to < body.length && " …"}
    </p>
  );
}

const when = (iso?: string | null) => iso
  ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";

/** One piece of evidence, opened: where it came from, the words in context, and why. */
export function EvidenceDetail({ e, envelopeId, onUse }:
  { e: Evidence; envelopeId?: number; onUse?: (e: Evidence) => void }) {
  const meta = e.kind === "text" || e.kind === "reply"
    ? `${e.kind === "reply" ? "Client's reply" : CHANNEL[e.channel ?? ""] ?? "Document"} · ${e.file_name ?? ""}${e.received_at ? ` · ${when(e.received_at)}` : ""}`
    : e.kind === "comparable" ? "Past projects" : e.kind === "rule" ? "Qualification rule"
    : e.kind === "setting" ? "Practice setting" : "Worked out by the engine";
  return (
    <div className="bw-source-detail">
      <div className="bw-source-detail__meta"><span className="bw-label">{meta}</span></div>
      {(e.kind === "text" || e.kind === "reply" || (e.kind === "engine" && e.quote)) && <QuotedText e={e} />}
      {e.kind === "comparable" && !!e.ref?.comparables?.length && (
        <div className="bw-chips">{e.ref.comparables.map((c) => <span key={c} className="bw-badge">{c}</span>)}</div>
      )}
      <p className="bw-why"><strong>Why:</strong> {e.why}</p>
      <div className="bw-row">
        {onUse && e.field_value_id && <button type="button" className="bw-btn bw-btn--soft bw-btn--sm" onClick={() => onUse(e)}>Use this value</button>}
        {e.document_id && e.has_pdf && !e.has_text && envelopeId &&
          <a className="bw-btn bw-btn--outline bw-btn--sm" href={`#/rfp/${envelopeId}`}>Open the document</a>}
      </div>
    </div>
  );
}
