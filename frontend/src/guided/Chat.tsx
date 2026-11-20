import { useContext, useEffect, useRef, useState } from "react";
import { Session } from "../session";
import { ArrowRight, Check, ChevronDown, Copy, Download, Flag, Paperclip, Sparkles, X } from "lucide-react";
import { AgentTrace } from "@/components/ui/agent-trace";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { Dossier } from "./Dossier";
import { PromptInput, type PromptInputHandle } from "@/components/ui/ai-chat-input";
import { api, chat, guided, type Gate1, type ChatAction, type ChatMessage, type ChatThread, type Evidence, type RequestCard } from "../api";
import { Markdown } from "../components/Markdown";
import { pillText, sourceOf, type SourceSpec } from "./fieldState";
import { Badge, ConfirmCard, SourcePill, useDesktop } from "./kit";
import { EvidenceDetail, Sheet, errorText } from "./ui";

const EFFORT_LABELS = ["Quick", "Balanced", "Thorough"];
const EFFORTS = ["low", "medium", "high"] as const;
const READING = ["received", "reading", "extracting"];

/** Wait until uploaded files have been read (about half a minute), so the assistant sees what was found. */
async function readingDone(id: number, limitMs = 150_000): Promise<void> {
  const until = Date.now() + limitMs;
  while (Date.now() < until) {
    const d = await api.detail(id);
    if (!READING.includes(d.envelope.status)) return;
    await new Promise((r) => setTimeout(r, 1500));
  }
}


/** A conversation with the assistant. On the Ask screen it starts empty; on a request it continues that
 * request's last conversation. Answers show their sources; anything that changes a record is a confirm card. */
export function ChatPanel({ envelopeId, threadId, onThread, onChanged, autoFocus, waiting, onStarted, withDossier,
                            dossierOpen = true, onDossier }: {
  envelopeId?: number; threadId?: number; onThread?: (t: ChatThread) => void; onChanged?: () => void; autoFocus?: boolean;
  /** Requests waiting on a decision, for the "Decide" step on the first screen. */
  waiting?: number;
  /** Told when the conversation starts (the first message goes in) or is loaded with messages. */
  onStarted?: (started: boolean) => void;
  /** Show the request's dossier beside the conversation once it is about a request (the Ask screen). */
  withDossier?: boolean;
  /** The page owns the open/closed toggle (an icon in its top bar); it is told when there is a dossier to show. */
  dossierOpen?: boolean;
  onDossier?: (available: boolean) => void;
}) {
  const [thread, setThread] = useState<ChatThread | null>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [source, setSource] = useState<Evidence[] | null>(null);
  const log = useRef<HTMLDivElement>(null);
  const box = useRef<PromptInputHandle>(null);

  useEffect(() => {
    if (threadId) chat.thread(threadId).then((t) => { setThread(t); onThread?.(t); }, (e) => setErr(errorText(e)));
    else if (envelopeId) chat.forRequest(envelopeId).then((t) => t && setThread(t), () => undefined);
  }, [envelopeId, threadId]);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" }); }, [thread, pending]);

  const send = async (message: string, effort = 0, files: File[] = []) => {
    if (!message.trim() && !files.length) return;
    setDraft("");
    setErr(null);
    let attached: number | undefined;
    let upload_ms: number | undefined, waited_ms: number | undefined;
    const clicked = performance.now();  // the trace shows the whole wait from here, not just the answer
    try {
      if (files.length) {  // files become a request, read like any other, then the assistant talks about it
        setPending(`__files__${files.length}`);
        attached = (await guided.upload(files, "upload")).envelope_id;
        upload_ms = Math.round(performance.now() - clicked);
        await readingDone(attached);
        waited_ms = Math.round(performance.now() - clicked);
        message = message.trim() || `I've added ${files.length === 1 ? "a file" : `${files.length} files`}: ${files.map((f) => f.name).join(", ")}. What's in it and what's missing?`;
      }
      setPending(message);
      const t = await chat.send({ message, thread_id: thread?.thread_id, effort: EFFORTS[effort],
                                  envelope_id: attached ?? envelopeId ?? thread?.envelope_id ?? undefined, attached: !!attached,
                                  upload_ms, waited_ms });
      setThread(t);
      onThread?.(t);
      onChanged?.();
    } catch (e) { setErr(errorText(e)); setDraft(message); } finally { setPending(null); }
  };
  const act = async (a: ChatAction, choice: "confirm" | "cancel") => {
    setErr(null);
    setPending(choice === "confirm" ? "__action__" : null);
    try { setThread(await chat.decide(a.action_id, choice)); onChanged?.(); } catch (e) { setErr(errorText(e)); } finally { setPending(null); }
  };

  const messages = thread?.messages ?? [];
  // the request the conversation is about now: the one it was opened on, read in, or last showed
  const lastCard = [...messages].reverse().flatMap((m) => m.cards ?? [])[0]?.envelope_id;
  const focus = envelopeId ?? thread?.envelope_id ?? lastCard ?? undefined;
  const [gate, setGate] = useState<Gate1 | null>(null);
  const [edits, setEdits] = useState(0);  // a value set in the dossier: read the request's state again
  useEffect(() => {
    if (!focus) { setGate(null); return; }
    guided.gate1(focus).then(setGate, () => setGate(null));
  }, [focus, messages.length, edits]);
  const started = messages.length > 0 || !!pending;
  useEffect(() => { onStarted?.(started); }, [started]);  // eslint-disable-line react-hooks/exhaustive-deps
  const desktop = useDesktop();
  const [sheetDossier, setSheetDossier] = useState(false);
  const dossier = !!withDossier && messages.length > 0 && !!gate && !!focus;
  useEffect(() => { onDossier?.(dossier); }, [dossier]);  // eslint-disable-line react-hooks/exhaustive-deps
  // with the dossier beside the chat, decisions and links live in its footer: the chat keeps the questions
  const steps = nextSteps({ gate, focus, started: messages.length > 0, waiting, onRequest: !!envelopeId })
    .filter((x) => !(dossier && (x.kind === "go" || ("decides" in x && x.decides))));
  // a source chip in the dossier finds its pill in the conversation; failing that, it opens the source itself
  const openSource = (pill: string, evidence: Evidence[]) => {
    const hit = [...(log.current?.querySelectorAll<HTMLElement>(".bw-source") ?? [])].reverse()
      .find((el) => el.textContent?.trim() === pill);
    if (hit) {
      hit.scrollIntoView({ behavior: "smooth", block: "center" });
      hit.dataset.flash = "";
      setTimeout(() => { delete hit.dataset.flash; }, 1600);
    } else setSource(evidence);
  };
  const runStep = (x: Step) => {
    if (x.kind === "ask") send(x.text);
    else if (x.kind === "focus") box.current?.focus();
    else if (x.kind === "files") box.current?.addFiles();
  };
  const latestPending = [...messages].reverse().flatMap((m) => m.actions ?? []).find((a) => a.status === "pending")?.action_id;
  const long = (pending?.length ?? 0) > 120;
  const column = (
    <div className="bw-chat">
      {dossier && !desktop && (
        <button type="button" className="bw-step bw-step--go bw-dossier-open" onClick={() => setSheetDossier(true)}>
          Request details<ArrowRight className="bw-icon bw-icon--sm" aria-hidden />
        </button>
      )}
      <div className="bw-chat__log" ref={log} aria-live="polite" data-empty={(messages.length === 0 && !pending) || undefined}>
        {messages.map((m) => <Message key={m.id} m={m} onAct={act} onSource={setSource} latest={latestPending} />)}
        {pending && pending !== "__action__" && !pending.startsWith("__files__") && (
          <div className="bw-turn bw-turn--user">
            <div className="bw-msg--user">{pending.length > 300 ? `${pending.slice(0, 300)}…` : pending}</div>
            <Avatar who="user" />
          </div>
        )}
        {pending && <div className="bw-turn bw-turn--ai"><Avatar who="assistant" /><Thinking label={pending === "__action__" ? "Working on it" : pending.startsWith("__files__") ? "Reading the files"
                                      : long ? "Reading the request" : "Thinking"}
                              hint={long || pending.startsWith("__files__") ? "This takes under a minute." : undefined} /></div>}
      </div>
      {err && <p role="alert" className="bw-body bw-chat__err">{err}</p>}
      {messages.length > 0 && <Steps steps={steps} disabled={!!pending} onRun={runStep} />}
      <div className="bw-composer">
        <PromptInput ref={box} id={`chat-${envelopeId ?? "home"}`} value={draft} onChange={setDraft} busy={!!pending}
                     autoFocus={autoFocus} efforts={EFFORT_LABELS} collapsedWidth={messages.length ? 4000 : 420}
                     placeholder={envelopeId ? "Ask about this request, or add the client's files…" : "Ask anything, paste a request or add files…"}
                     onSubmit={(v, m) => send(v, m.effortIndex, m.attachments)} />
      </div>
      {messages.length === 0 && !pending && <Steps steps={steps} disabled={false} onRun={runStep} />}
      <Sheet open={!!source} title="Where this came from" onClose={() => setSource(null)}>
        {source?.map((e, i) => <EvidenceDetail key={i} e={e} />)}
      </Sheet>
      {dossier && !desktop && (
        <Sheet open={sheetDossier} title="Request details" onClose={() => setSheetDossier(false)}>
          <Dossier id={focus!} gate={gate!} busy={!!pending} refresh={messages.length + edits} onChanged={() => setEdits((n) => n + 1)}
                   say={(t) => { setSheetDossier(false); send(t); }} openSource={(_, ev) => setSource(ev)} />
        </Sheet>
      )}
    </div>
  );
  if (!(dossier && desktop && dossierOpen)) return column;
  return (
    <PanelGroup direction="horizontal" autoSaveId="bw-dossier" className="bw-split">
      <Panel id="chat" order={1} minSize={42} defaultSize={62}>{column}</Panel>
      <PanelResizeHandle className="bw-split__handle" aria-label="Resize the dossier" />
      <Panel id="dossier" order={2} minSize={28} maxSize={55} defaultSize={38} className="bw-split__aside">
        <Dossier id={focus!} gate={gate!} busy={!!pending} refresh={messages.length + edits} say={send} openSource={openSource}
                 onChanged={() => setEdits((n) => n + 1)} />
      </Panel>
    </PanelGroup>
  );
}

function Avatar({ who }: { who: "user" | "assistant" }) {
  const me = useContext(Session);
  if (who === "assistant") {
    return <span className="bw-avatar bw-avatar--ai" role="img" aria-label="Assistant"><Sparkles aria-hidden /></span>;
  }
  const name = me?.user.name ?? "You";
  const initials = name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  return <span className="bw-avatar" role="img" aria-label={name} title={name}>{initials}</span>;
}

function Message({ m, onAct, onSource, latest }: { m: ChatMessage; onAct: (a: ChatAction, c: "confirm" | "cancel") => void;
  onSource: (e: Evidence[]) => void; latest?: number }) {
  if (m.role === "user") {
    return (
      <div className="bw-turn bw-turn--user">
        <div className="bw-msg--user">{m.text.length > 400 ? `${m.text.slice(0, 400)}…` : m.text}</div>
        <Avatar who="user" />
      </div>
    );
  }
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
    <div className="bw-turn bw-turn--ai">
    <Avatar who="assistant" />
    <div className="bw-msg--assistant">
      {m.text && <Markdown text={m.text.replace(/\s*\[ev:[^\]]*\]/g, "")} />}
      {m.cards?.map((c) => <RequestSummary key={c.envelope_id} c={c} />)}
      {m.actions?.map((a) => (
        <ConfirmCard key={a.action_id} status={a.status} before={a.before} after={a.after} summary={a.summary}
                     reason={a.reason} primary={a.action_id === latest}
                     onConfirm={() => onAct(a, "confirm")} onCancel={() => onAct(a, "cancel")} />
      ))}
      {sources.length > 0 && (
        <div className="bw-row bw-chat__sources">
          <span className="bw-label">Sources</span>
          {dedupe(sources).map(([src, es]) => <SourcePill key={pillText(src)} source={src} onOpen={() => onSource(es)} />)}
        </div>
      )}
      {m.trace && m.trace.spans.length > 0 && <HowIGotThis trace={m.trace} id={m.id} />}
    </div>
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

/** While the assistant works: what it is doing and for how long, so a long read never looks stuck. */
function Thinking({ label, hint }: { label: string; hint?: string }) {
  const [t, setT] = useState(0);
  useEffect(() => {
    const start = performance.now();
    const i = setInterval(() => setT(performance.now() - start), 100);
    return () => clearInterval(i);
  }, []);
  return (
    <div className="bw-thinking" role="status">
      <Sparkles className="bw-icon bw-icon--sm bw-thinking__icon" aria-hidden />
      <span className="bw-thinking__label">{label}</span>
      <span className="bw-reading" aria-hidden><i /><i /><i /></span>
      <span className="bw-thinking__clock">{(t / 1000).toFixed(1)}s</span>
      {hint && <span className="bw-muted bw-thinking__hint">{hint}</span>}
    </div>
  );
}

/** "How I got this": the answer's steps (model calls, tools, the figure check) on a time axis that replays. */
function HowIGotThis({ trace, id }: { trace: NonNullable<ChatMessage["trace"]>; id: number }) {
  const [open, setOpen] = useState(false);
  // tools the assistant chose to use; the setup, the upload, the reading and the figure check are listed apart
  const SETUP = ["load_conversation", "upload_files", "read_files", "check_figures"];
  const reading = trace.spans.find((s) => s.label === "read_files");
  const inReading = new Set(trace.spans.filter((s) => s.parentId && (s.parentId === reading?.id || s.parentId.startsWith("r"))).map((s) => s.id));
  const tools = trace.spans.filter((s) => s.kind !== "model" && !SETUP.includes(s.label) && !inReading.has(s.id)).length;
  const stopped = trace.spans.some((s) => s.label === "check_figures" && s.status === "error");
  return (
    <div className="bw-how" data-open={open || undefined}>
      <button type="button" className="bw-how__toggle" aria-expanded={open} aria-controls={`how-${id}`} onClick={() => setOpen(!open)}>
        <Sparkles className="bw-icon bw-icon--sm" aria-hidden />
        <span>How I got this</span>
        <span className="bw-how__facts">{(trace.duration / 1000).toFixed(1)}s{reading ? ` · reading ${((reading.end - reading.start) / 1000).toFixed(1)}s` : ""} · {tools} tool{tools === 1 ? "" : "s"}{stopped ? " · 1 answer stopped" : ""}</span>
        <ChevronDown className="bw-icon bw-icon--sm bw-how__chev" aria-hidden />
      </button>
      {open && (
        <div id={`how-${id}`} className="bw-how__body">
          <AgentTrace spans={trace.spans} duration={trace.duration} runId={`answer ${id}`} model={trace.model}
                      autoPlay speed={Math.max(1, trace.duration / 4000)} rowHeight={32} labelWidth={170} />
          <p className="bw-muted bw-how__note">Every figure in the answer came from one of these tools; the last step checks that, and stops the answer if not.</p>
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- next steps
   The buttons under the conversation follow where it has got to. Before anything is asked: the usual ways in.
   Once it is about a request: that request's next step (qualify, ask the client, approve the fee), a question
   for each flag that matters, and a way to open the request itself. */

type Step = { label: string; kind: "ask"; text: string; tone?: "flag"; decides?: boolean }
  | { label: string; kind: "go"; href: string } | { label: string; kind: "focus" | "files" };

const FLAG_QUESTION: Record<string, [string, string]> = {
  repeat_client: ["What did we quote them before?", "What did we quote this client before, and did we win?"],
  cross_entity: ["Which studio should run it?", "This project is outside the studio's country. Which studio should run it, and what changes?"],
  tight_deadline: ["How long do we have?", "How long do we have before the deadline, in working days?"],
  uncapped_liability: ["Uncapped liability: our position?", "The client asks for uncapped liability. What is our standard position?"],
  currency_unconfirmed: ["Which currency should we quote?", "Which currency should we quote this request in, and why?"],
  ambiguous_pricing_field: ["Which figure is right?", "Which values are unclear, and what does each one do to the fee?"],
};

function nextSteps({ gate, focus, started, waiting, onRequest }: {
  gate: Gate1 | null; focus?: number; started: boolean; waiting?: number; onRequest: boolean;
}): Step[] {
  if (!gate || !focus) {
    if (onRequest) return [{ label: "What's missing?", kind: "ask", text: "What's missing on this request?" }];
    const out: Step[] = [
      { label: "What needs a decision today?", kind: "ask", text: "What needs a decision today?" },
      { label: "Paste a WhatsApp request", kind: "focus" },
      { label: "Add the client's files", kind: "files" },
      { label: "Show requests from repeat clients", kind: "ask", text: "Show requests from repeat clients" },
    ];
    if (!started && waiting) out.push({ label: `Decide · ${waiting} waiting`, kind: "go", href: "#/decide" });
    if (!started) out.push({ label: "Past chats", kind: "go", href: "#/chats" });
    return out;
  }
  const steps: Step[] = [];
  const d = gate.decision?.decision;
  if (d === "qualify") {
    steps.push({ label: "Why is the fee what it is?", kind: "ask", text: "Why is the fee what it is?" },
               { label: "What could move the fee?", kind: "ask", text: "Which assumptions could move the fee most?" },
               { label: "Approve the fee", kind: "ask", text: "Approve the fee at the opening figure.", decides: true },
               { label: "Open the fee card", kind: "go", href: `#/r/${focus}/fee` });
  } else if (d === "ask") {
    steps.push({ label: "Add the client's reply", kind: "focus" },
               { label: "What are we waiting for?", kind: "ask", text: "What are we still waiting for from the client?" });
  } else if (d === "decline") {
    steps.push({ label: "Why did we decline it?", kind: "ask", text: "Why did we decline this request?" });
  } else if (gate.recommendation === "go") {
    steps.push({ label: "Qualify it", kind: "ask", text: "Qualify this request.", decides: true },
               { label: "Show the fee", kind: "ask", text: "Show me the fee card for this request." });
  } else if (gate.recommendation === "clarify") {
    steps.push({ label: "Write the questions for the client", kind: "ask", text: "Write the questions to send the client." },
               { label: "Why clarify first?", kind: "ask", text: "Why does this request need clarifying before pricing?" },
               { label: "Qualify it with the estimates", kind: "ask", text: "Qualify this request using the estimates.", decides: true });
  } else {
    steps.push({ label: "Why decline it?", kind: "ask", text: "Why is the suggestion to decline this request?" },
               { label: "Decline it", kind: "ask", text: "Decline this request with the suggested reason.", decides: true });
  }
  for (const r of [...gate.rules, ...gate.flags]) {
    const q = FLAG_QUESTION[r.name];
    if (q && steps.filter((x) => x.kind === "ask" && x.tone === "flag").length < 2) steps.push({ label: q[0], kind: "ask", text: q[1], tone: "flag" });
  }
  if (d !== "qualify" && !onRequest) steps.push({ label: "Open the request", kind: "go", href: `#/r/${focus}` });
  return steps.slice(0, 6);
}

function Steps({ steps, disabled, onRun, compact, lead }: { steps: Step[]; disabled: boolean; onRun: (s: Step) => void;
  /** On a phone during a conversation: the first step and a "more" button that opens the rest. */
  compact?: boolean;
  /** Something to put first in the row (on a phone: the button that opens the request's details). */
  lead?: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  if (!steps.length && !lead) return null;
  const folded = compact && !open && steps.length > 1;
  const shown = folded ? steps.slice(0, 1) : steps;
  const run = (x: Step) => { setOpen(false); onRun(x); };
  return (
    <div className="bw-suggestions" role="group" aria-label="Next steps" data-compact={compact || undefined}>
      {lead}
      {shown.map((x) => x.kind === "go" ? (
        <a key={x.label} className="bw-step bw-step--go" href={x.href}>{x.label}<ArrowRight className="bw-icon bw-icon--sm" aria-hidden /></a>
      ) : (
        <button key={x.label} type="button" className="bw-step" data-tone={"tone" in x ? x.tone : undefined} disabled={disabled}
                onClick={() => run(x)}>
          {"tone" in x && x.tone === "flag" && <Flag className="bw-icon bw-icon--sm" aria-hidden />}
          {x.kind === "files" && <Paperclip className="bw-icon bw-icon--sm" aria-hidden />}
          {x.label}
        </button>
      ))}
      {compact && steps.length > 1 && (
        <button type="button" className="bw-step bw-step--more" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Fewer" : `${steps.length - 1} more`}
          {!open && steps.some((x) => "tone" in x && x.tone === "flag") && <Flag className="bw-icon bw-icon--sm" aria-label="includes flags" />}
          <ChevronDown className="bw-icon bw-icon--sm bw-step__chev" aria-hidden />
        </button>
      )}
    </div>
  );
}

/** One pill per distinct source: two values read from the same words show once, and open both. */
function dedupe(sources: Evidence[]): [SourceSpec, Evidence[]][] {
  const out = new Map<string, [SourceSpec, Evidence[]]>();
  for (const e of sources) {
    const src = sourceOf(e);
    if (!src) continue;
    const k = pillText(src);
    out.set(k, [out.get(k)?.[0] ?? src, [...(out.get(k)?.[1] ?? []), e]]);
  }
  return [...out.values()];
}
