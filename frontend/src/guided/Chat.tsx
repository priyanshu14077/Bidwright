import { useEffect, useRef, useState } from "react";
import { ArrowRight, ArrowUp, Check, Copy, Download, X } from "lucide-react";
import { chat, type ChatAction, type ChatMessage, type ChatThread, type Evidence, type RequestCard } from "../api";
import { Markdown } from "../components/Markdown";
import { sourceOf } from "./fieldState";
import { Badge, ConfirmCard, SourcePill } from "./kit";
import { EvidenceDetail, Sheet, errorText } from "./ui";

const HOME_SUGGESTIONS = ["What needs a decision today?", "Paste a WhatsApp request", "Show requests from repeat clients"];
const REQUEST_SUGGESTIONS = ["What's missing?", "Why this fee?", "Show similar projects"];

/** A conversation with the assistant. On the Ask screen it starts empty; on a request it continues that
 * request's last conversation. Answers show their sources; anything that changes a record is a confirm card. */
export function ChatPanel({ envelopeId, onChanged, autoFocus }: { envelopeId?: number; onChanged?: () => void; autoFocus?: boolean }) {
  const [thread, setThread] = useState<ChatThread | null>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [source, setSource] = useState<Evidence[] | null>(null);
  const log = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (envelopeId) chat.forRequest(envelopeId).then((t) => t && setThread(t), () => undefined);
  }, [envelopeId]);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" }); }, [thread, pending]);

  const send = async (message: string) => {
    if (!message.trim()) return;
    setDraft("");
    setErr(null);
    setPending(message);
    try {
      const t = await chat.send({ message, thread_id: thread?.thread_id, envelope_id: envelopeId ?? thread?.envelope_id ?? undefined });
      setThread(t);
      onChanged?.();
    } catch (e) { setErr(errorText(e)); setDraft(message); } finally { setPending(null); }
  };
  const act = async (a: ChatAction, choice: "confirm" | "cancel") => {
    setErr(null);
    setPending(choice === "confirm" ? "__action__" : null);
    try { setThread(await chat.decide(a.action_id, choice)); onChanged?.(); } catch (e) { setErr(errorText(e)); } finally { setPending(null); }
  };

  const messages = thread?.messages ?? [];
  const latestPending = [...messages].reverse().flatMap((m) => m.actions ?? []).find((a) => a.status === "pending")?.action_id;
  const long = (pending?.length ?? 0) > 120;
  return (
    <div className="bw-chat">
      <div className="bw-chat__log" ref={log} aria-live="polite">
        {messages.length === 0 && !pending && (
          <p className="bw-muted bw-chat__hint">
            {envelopeId ? "Ask about this request: what's missing, why the fee is what it is, or tell me the client's answer."
              : "Paste what the client sent (a WhatsApp message, an email, your call notes) or ask about any request."}
          </p>
        )}
        {messages.map((m) => <Message key={m.id} m={m} onAct={act} onSource={setSource} latest={latestPending} />)}
        {pending && pending !== "__action__" && <div className="bw-msg--user">{pending.length > 300 ? `${pending.slice(0, 300)}…` : pending}</div>}
        {pending && (
          <div className="bw-msg--assistant bw-typing">
            <span className="bw-muted">{pending === "__action__" ? "Working on it…" : long ? "Reading the request… this takes under a minute." : "Thinking…"}</span>
          </div>
        )}
      </div>
      {err && <p role="alert" className="bw-body bw-chat__err">{err}</p>}
      <div className="bw-suggestions">
        {(envelopeId ? REQUEST_SUGGESTIONS : HOME_SUGGESTIONS).map((s) => (
          <button key={s} type="button" disabled={!!pending}
                  onClick={() => (s.startsWith("Paste") ? document.getElementById(`chat-${envelopeId ?? "home"}`)?.focus() : send(s))}>{s}</button>
        ))}
        {!envelopeId && <a className="bw-suggestions__link" href="#/new">Add files instead</a>}
      </div>
      <form className="bw-composer" onSubmit={(e) => { e.preventDefault(); send(draft); }}>
        <label className="bw-sr-only" htmlFor={`chat-${envelopeId ?? "home"}`}>Message</label>
        <textarea id={`chat-${envelopeId ?? "home"}`} className="bw-input bw-composer__input" rows={1} value={draft}
                  autoFocus={autoFocus} autoComplete="off" placeholder={envelopeId ? "Ask about this request…" : "Ask anything or paste a request…"}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(draft); } }} />
        <button type="submit" className="bw-composer__send" aria-label="Send" disabled={!draft.trim() || !!pending}>
          <ArrowUp className="bw-icon" />
        </button>
      </form>
      <Sheet open={!!source} title="Where this came from" onClose={() => setSource(null)}>
        {source?.map((e, i) => <EvidenceDetail key={i} e={e} />)}
      </Sheet>
    </div>
  );
}

function Message({ m, onAct, onSource, latest }: { m: ChatMessage; onAct: (a: ChatAction, c: "confirm" | "cancel") => void;
  onSource: (e: Evidence[]) => void; latest?: number }) {
  if (m.role === "user") return <div className="bw-msg--user">{m.text.length > 400 ? `${m.text.slice(0, 400)}…` : m.text}</div>;
  if (m.role === "system") {
    return (
      <div className="bw-msg--system" data-status={m.status}>
        {m.status === "confirmed" ? <Check className="bw-icon bw-icon--sm" /> : <X className="bw-icon bw-icon--sm" />}
        <span>{m.text}</span>
        {m.downloads?.map((d) => (
          <a key={d.url} className="bw-btn bw-btn--approve bw-btn--sm" href={d.url}><Download className="bw-icon bw-icon--sm" />{d.label}</a>
        ))}
        {m.cards?.map((c) => <RequestSummary key={c.envelope_id} c={c} />)}
      </div>
    );
  }
  const sources = Object.values(m.sources ?? {});
  return (
    <div className="bw-msg--assistant">
      {m.text && <Markdown text={m.text.replace(/\s*\[ev:\d+\]/g, "")} />}
      {m.cards?.map((c) => <RequestSummary key={c.envelope_id} c={c} />)}
      {m.actions?.map((a) => (
        <ConfirmCard key={a.action_id} status={a.status} before={a.before} after={a.after} summary={a.summary}
                     reason={a.reason} primary={a.action_id === latest}
                     onConfirm={() => onAct(a, "confirm")} onCancel={() => onAct(a, "cancel")} />
      ))}
      {sources.length > 0 && (
        <div className="bw-row bw-chat__sources">
          <span className="bw-label">Sources</span>
          {sources.map((e) => {
            const src = sourceOf(e);
            return src && <SourcePill key={e.id} source={src} onOpen={() => onSource([e])} />;
          })}
        </div>
      )}
    </div>
  );
}

const OUTCOME = { go: "found", clarify: "estimated", "no-go": "missing" } as const;

function RequestSummary({ c }: { c: RequestCard }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { await navigator.clipboard.writeText(c.message); setCopied(true); setTimeout(() => setCopied(false), 2000); };
  return (
    <div className="bw-card bw-chat__card">
      <div className="bw-row">
        <strong className="bw-body">{c.title ?? `Request ${c.envelope_id}`}</strong>
        <Badge status={OUTCOME[c.recommendation]}>{c.headline}</Badge>
      </div>
      {c.why && <p className="bw-muted">{c.why}</p>}
      <div className="bw-badges">
        <Badge status="found">✓ {c.counts.found ?? 0} found</Badge>
        <Badge status="estimated">≈ {(c.counts.estimated ?? 0) + (c.counts.ambiguous ?? 0)} estimated</Badge>
        <Badge status="missing">? {c.counts.missing ?? 0} missing</Badge>
      </div>
      {c.questions.length > 0 && (
        <ol className="bw-chat__questions">{c.questions.map((q) => <li key={q.field}>{q.question}</li>)}</ol>
      )}
      <div className="bw-row">
        {c.questions.length > 0 && (
          <button type="button" className="bw-btn bw-btn--soft bw-btn--sm" onClick={copy}>
            <Copy className="bw-icon bw-icon--sm" />{copied ? "Copied" : `Copy ${c.questions.length} question${c.questions.length === 1 ? "" : "s"}`}
          </button>
        )}
        <a className="bw-btn bw-btn--soft bw-btn--sm" href={`#/r/${c.envelope_id}`}>Open the request <ArrowRight className="bw-icon bw-icon--sm" /></a>
      </div>
    </div>
  );
}
