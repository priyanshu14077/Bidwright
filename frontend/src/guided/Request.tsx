import { useCallback, useEffect, useRef, useState, type CSSProperties, type JSX } from "react";
import { cameFrom, placeName } from "./trail";
import { ArrowRight, Check, ChevronLeft, Copy, MessageCircle, Share2 } from "lucide-react";
import {
  api, guided, type Detail, type Evidence, type FeeCard, type Gate1, type GateField,
  type ProposalStep as ProposalData,
} from "../api";
import { useCan } from "../session";
import { ChatPanel } from "./Chat";
import { fieldState, sourceOf, type SourceSpec } from "./fieldState";
import { ActionBar, Badge, Field, Flag, HeroCard, PinnedCard, RangeBar, SourcePill, isDesktop } from "./kit";
import { ProposalStep } from "./ProposalStep";
import { EvidenceDetail, Sheet, errorText, money, short } from "./ui";

const READING = ["received", "reading", "extracting"];
export type RequestStep = "have" | "fee" | "proposal";
type SheetView = { title: string; evidence: Evidence[]; field?: GateField };

const FALLBACK: Record<string, string> = {
  gfa_m2: "What is the total floor area (GFA) of the building?", site_area_m2: "How big is the site or plot?",
  stage_package: "Which design stages would you like us to cover?", services: "Which services do you need from us?",
  typology: "What kind of building is it?", city: "Which city is the project in?", country: "Which country is the project in?",
  submission_deadline: "When do you need our proposal?", billing_currency: "Which currency should we quote in?",
  client_name: "Which company should we address the proposal to?", quality_tier: "What quality level is the project aiming for?",
  client_status: "Have we worked with this client before?",
};

/** The fields whose status or value changed between two readings: they settle visibly. */
function changedFields(before: Gate1 | null, after: Gate1): Set<string> {
  if (!before) return new Set();
  const was = Object.fromEntries(before.fields.map((f) => [f.name, `${f.status}|${JSON.stringify(f.value)}`]));
  return new Set(after.fields.filter((f) => was[f.name] !== `${f.status}|${JSON.stringify(f.value)}`).map((f) => f.name));
}

export function RequestPage({ id, step }: { id: number; step: RequestStep }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [gate, setGateRaw] = useState<Gate1 | null>(null);
  const [fee, setFee] = useState<FeeCard | null>(null);
  const [proposal, setProposal] = useState<ProposalData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sheet, setSheet] = useState<SheetView | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const [arriving, setArriving] = useState(false);
  const prev = useRef<Gate1 | null>(null);
  const wasReading = useRef(false);

  const setGate = useCallback((g: Gate1) => {
    const diff = changedFields(prev.current, g);
    prev.current = g;
    setGateRaw(g);
    if (diff.size) {
      setChanged(diff);
      setTimeout(() => setChanged(new Set()), 1200);
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const d = await api.detail(id);
      setDetail(d);
      const reading = READING.includes(d.envelope.status);
      if (reading || d.envelope.status === "failed") { wasReading.current = wasReading.current || reading; return; }
      const [g, f, p] = await Promise.all([guided.gate1(id), guided.fee(id), guided.proposal(id)]);
      if (wasReading.current && !prev.current) {  // the first reading finished: the fields arrive one by one
        setArriving(true);
        setTimeout(() => setArriving(false), 1500);
      }
      wasReading.current = false;
      setGate(g);
      setFee(f);
      setProposal(p);
      setErr(null);
    } catch (e) { setErr(errorText(e)); }
  }, [id, setGate]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => setSheet(null), [step]);
  const reading = !!detail && READING.includes(detail.envelope.status);
  useEffect(() => {
    if (!reading) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [reading, load]);

  const title = (detail?.record.project_name?.value as string | undefined) ?? detail?.envelope.title ?? `Request ${id}`;
  const index = { have: 2, fee: 3, proposal: 4 }[step];
  const name = { have: "What we have", fee: "Fee", proposal: "Proposal" }[step];
  const feeDone = !!fee?.decision && fee.decision.decision !== "reject";
  // back goes where the person came from (a chat, Decide, How we price), skipping this request's own steps
  const from = cameFrom((h) => h === `#/r/${id}` || h.startsWith(`#/r/${id}/`));
  const back = from ?? "#/decide";

  return (
    <>
      <header className="bw-header bw-request-header">
        <a className="bw-icon-btn bw-back" href={back} aria-label={`Back to ${placeName(back)}`}><ChevronLeft className="bw-icon" /></a>
        {from && (
          <a className="bw-btn bw-btn--outline bw-btn--sm bw-desktop-only bw-back-to" href={from}>
            <ChevronLeft className="bw-icon bw-icon--sm" />Back to {placeName(from)}
          </a>
        )}
        <div className="bw-header__text">
          <div className="bw-title-sm">{title}</div>
          <div className="bw-caps bw-mobile-only">Step {index} of 4 · {name}</div>
          {detail && <div className="bw-muted bw-desktop-only">{describe(detail)}</div>}
        </div>
        <ol className="bw-steps" aria-label="Steps">
          <li data-done>Request</li>
          <li aria-current={step === "have" ? "step" : undefined}
              data-done={(step !== "have" && (gate?.decision?.decision === "qualify" || feeDone)) || undefined}>
            <a href={`#/r/${id}`}>What we have</a></li>
          <li aria-current={step === "fee" ? "step" : undefined} data-done={(step !== "fee" && feeDone) || undefined}>
            <a href={`#/r/${id}/fee`}>Fee</a></li>
          <li aria-current={step === "proposal" ? "step" : undefined}><a href={`#/r/${id}/proposal`}>Proposal</a></li>
        </ol>
        <button type="button" className="bw-btn bw-btn--soft bw-btn--sm bw-desktop-only" onClick={() => setChatOpen(true)}>
          <MessageCircle className="bw-icon bw-icon--sm" />Ask
        </button>
      </header>
      {err && <p role="alert" className="bw-body">{err}</p>}
      {!detail ? <p className="bw-muted">Loading…</p>
        : reading && !gate ? <Reading detail={detail} />
        : detail.envelope.status === "failed" ? <Failed id={id} message={detail.envelope.error} onRetry={load} />
        : !gate ? <p className="bw-muted">Loading…</p>
        : step === "have" ? <WhatWeHave id={id} gate={gate} reading={reading} changed={changed} arriving={arriving}
                                        onChanged={load} onSheet={setSheet} setGate={setGate} />
        : step === "fee" ? fee && <FeeView id={id} card={fee} onSheet={setSheet}
                                           setCard={(c) => { setFee(c); guided.proposal(id).then(setProposal); }} />
        : <ProposalStep id={id} step={proposal} setStep={setProposal} />}
      <p className="bw-muted"><a href={`#/rfp/${id}`}>Details: documents, every extracted value and the audit trail</a></p>
      <button type="button" className="bw-chat-fab" onClick={() => setChatOpen(true)}><MessageCircle className="bw-icon" />Ask</button>
      <Sheet open={chatOpen} title="Ask about this request" onClose={() => setChatOpen(false)}>
        <ChatPanel envelopeId={id} onChanged={load} />
      </Sheet>
      <Sheet open={!!sheet} title={sheet?.title ?? ""} onClose={() => setSheet(null)}>
        {sheet?.evidence.map((e, i) => (
          <EvidenceDetail key={i} e={e} envelopeId={id}
                          onUse={sheet.field?.status === "ambiguous" && detail?.envelope.status !== "confirmed" ? async (x) => {
                            await guided.resolve(id, sheet.field!.name, x.field_value_id!, "Chosen on the request screen");
                            setSheet(null);
                            await load();
                          } : undefined} />
        ))}
      </Sheet>
    </>
  );
}

function describe(d: Detail): string {
  const e = d.envelope;
  const channel = { whatsapp: "WhatsApp", email: "Email", notes: "Call notes", upload: "Upload", sharepoint: "SharePoint" }[e.channel] ?? e.channel;
  const docs = d.documents.filter((x) => !x.parent_document_id).length;
  return `${channel} · received ${new Date(e.received_at).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short" })}${docs > 1 ? ` · ${docs} files` : ""}`;
}

function Reading({ detail }: { detail: Detail }) {
  return (
    <HeroCard label="Reading">
      <span className="bw-caps">Suggested next step</span>
      <h2 className="bw-title">Reading the request <span className="bw-reading" aria-hidden><i /><i /><i /></span></h2>
      <p className="bw-lead">Every message and file is read, and the engine checks what pricing needs. Under a minute.</p>
      <ul className="bw-stack bw-files">{detail.documents.map((d) => <li key={d.document_id} className="bw-muted">{d.file_name}</li>)}</ul>
    </HeroCard>
  );
}

function Failed({ id, message, onRetry }: { id: number; message?: string | null; onRetry: () => void }) {
  return (
    <HeroCard label="Could not be read">
      <h2 className="bw-title">This request could not be read</h2>
      <p className="bw-muted">{message}</p>
      <button type="button" className="bw-btn bw-btn--primary" onClick={() => api.rerun(id).then(onRetry)}>Read it again</button>
    </HeroCard>
  );
}

// ---------------------------------------------------------------- What we have (gate 1)

const HEADLINE = { go: "Ready to price", clarify: "Clarify before pricing", "no-go": "Decline" };
const SUGGESTED = { go: "qualify", clarify: "ask", "no-go": "decline" } as const;
const DECISION_WORD = { qualify: "Qualify", ask: "Clarify", decline: "Decline" } as const;

function WhatWeHave({ id, gate, reading, changed, arriving, onChanged, onSheet, setGate }: {
  id: number; gate: Gate1; reading: boolean; changed: Set<string>; arriving: boolean; onChanged: () => Promise<unknown>;
  onSheet: (s: SheetView) => void; setGate: (g: Gate1) => void;
}) {
  const can = useCan();
  const [copied, setCopied] = useState(false);
  const [declining, setDeclining] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const worded = Object.fromEntries(gate.questions.items.map((q) => [q.field, q.question]));
  const askRows = [
    ...gate.questions.items.map((q) => gate.fields.find((f) => f.name === q.field)).filter((f): f is GateField => !!f),
    ...gate.fields.filter((f) => (f.status === "missing" || f.status === "ambiguous") && !(f.name in worded)),
  ];
  const asked = new Set(askRows.map((f) => f.name));
  const estimated = gate.fields.filter((f) => f.status === "estimated" && !asked.has(f.name));
  const found = gate.fields.filter((f) => f.status === "found");
  const counts = { found: found.length, estimated: gate.fields.filter((f) => f.status === "estimated" || f.status === "ambiguous").length,
                   missing: gate.fields.filter((f) => f.status === "missing").length };
  const decided = gate.decision?.decision;
  const pressed = decided ?? SUGGESTED[gate.recommendation];
  const why = gate.rules[0]?.why ?? "Everything the fee needs is stated or estimated from similar projects.";
  const n = gate.questions.items.length;

  const copy = async () => {
    await navigator.clipboard.writeText(gate.questions.message);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  const decide = async (decision: "qualify" | "ask" | "decline", reason?: string, note?: string) => {
    setBusy(true);
    setErr(null);
    try {
      setGate(await guided.decide(id, { decision, reason, note }));
      setDeclining(false);
      if (decision === "qualify") location.hash = `#/r/${id}/fee`;
    } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };
  const row = (f: GateField, k: number, question?: string) => {
    const s = fieldState(f);
    return <Field key={f.name} label={f.label} status={s.status} value={s.value} range={s.range} source={s.source}
                  evidence={s.evidence} confidence={s.confidence} note={s.note} question={question} envelopeId={id}
                  changed={changed.has(f.name)} arriving={arriving} i={k}
                  onSheet={(evidence, label) => onSheet({ title: label, evidence, field: f })} />;
  };

  return (
    <div className="bw-g1 bw-enter">
      <div className="bw-g1__col bw-g1__left">
        {askRows.length > 0 && (
          <PinnedCard title="Ask the client" className="bw-g1__ask" i={1}>
            {askRows.map((f, k) => row(f, k, worded[f.name] ?? FALLBACK[f.name]))}
          </PinnedCard>
        )}
        {can("intake") && <Reply id={id} onDone={onChanged} reading={reading} />}
        {estimated.length > 0 && (
          <PinnedCard title="We estimated" className="bw-g1__est" i={3}>
            {estimated.map((f, k) => row(f, askRows.length + k))}
          </PinnedCard>
        )}
        <PinnedCard title="Found in the request" className="bw-g1__found" i={4}>
          {found.slice(0, 4).map((f, k) => row(f, askRows.length + estimated.length + k))}
          {found.length > 4 && (
            <details className="bw-details">
              <summary>{found.length - 4} more found</summary>
              <div className="bw-stack bw-details__body">{found.slice(4).map((f, k) => row(f, k))}</div>
            </details>
          )}
        </PinnedCard>
      </div>
      <div className="bw-g1__col bw-g1__right">
        <OriginalMessage gate={gate} />
        <HeroCard label="Suggested next step" className="bw-g1__hero" i={0}>
          <span className="bw-caps">Suggested next step</span>
          {reading ? (
            <h2 className="bw-title">Reading the reply <span className="bw-reading" aria-hidden><i /><i /><i /></span></h2>
          ) : <h2 className="bw-title">{HEADLINE[gate.recommendation]}</h2>}
          <p className="bw-body">{why}</p>
          <div className="bw-badges">
            <Badge status="found" changed={changed.size > 0}>✓ {counts.found} found</Badge>
            <Badge status="estimated" changed={changed.size > 0}>≈ {counts.estimated} estimated</Badge>
            <Badge status="missing" changed={changed.size > 0}>? {counts.missing} missing</Badge>
          </div>
          {n > 0 && gate.recommendation !== "no-go" ? (
            <div className="bw-row bw-nowrap">
              <button type="button" className="bw-btn bw-btn--primary bw-grow" onClick={copy}>
                <Copy className="bw-icon" />{copied ? "Copied" : `Copy ${n} question${n === 1 ? "" : "s"} for WhatsApp`}
              </button>
              {"share" in navigator && (
                <button type="button" className="bw-icon-btn" aria-label="Share the questions"
                        onClick={() => navigator.share?.({ text: gate.questions.message })}><Share2 className="bw-icon" /></button>
              )}
            </div>
          ) : gate.recommendation !== "no-go" && (
            <a className="bw-btn bw-btn--primary bw-btn--block" href={`#/r/${id}/fee`}>See the fee <ArrowRight className="bw-icon" /></a>
          )}
          {can("review") && (
            <div className="bw-segmented" role="group" aria-label="Decision">
              {(["qualify", "ask", "decline"] as const).map((d) => (
                <button key={d} type="button" aria-pressed={pressed === d} disabled={busy}
                        onClick={() => (d === "decline" ? setDeclining(true) : decide(d))}>{DECISION_WORD[d]}</button>
              ))}
            </div>
          )}
          <p className="bw-muted">
            {decided ? `${{ qualify: "Qualified", ask: "Waiting for the client", decline: `Declined: ${gate.decision?.reason}` }[decided]}, by ${gate.decision?.decided_by}.`
              : `Suggested: ${DECISION_WORD[SUGGESTED[gate.recommendation]]}. You decide.`}
          </p>
          {n > 0 && (
            <details className="bw-details">
              <summary>Preview the message</summary>
              <p className="bw-details__body bw-message-preview">{gate.questions.message}</p>
            </details>
          )}
          {err && <p role="alert" className="bw-body">{err}</p>}
        </HeroCard>
      </div>
      <ReasonSheet open={declining} title="Decline this request" reasons={gate.decline_reasons} cta="Decline"
                   onClose={() => setDeclining(false)} onConfirm={(reason, note) => decide("decline", reason, note)}
                   footnote="The recommendation and your reason stay with the request." />
    </div>
  );
}

/** Desktop: the message as it arrived, with every quoted phrase marked. */
function OriginalMessage({ gate }: { gate: Gate1 }) {
  const quoted = gate.fields.flatMap((f) => f.evidence).filter((e) => (e.kind === "text" || e.kind === "reply") && e.has_text && e.span);
  const docs = [...new Set(quoted.map((e) => e.document_id!))];
  const key = docs.join(",");
  const [texts, setTexts] = useState<Record<number, string>>({});
  useEffect(() => {
    key.split(",").filter(Boolean).map(Number).forEach((d) =>
      guided.documentText(d).then((t) => setTexts((x) => ({ ...x, [d]: t })), () => undefined));
  }, [key]);
  if (!docs.length) return null;
  return (
    <section className="bw-card bw-desktop-only bw-g1__message" aria-label="Original message">
      <h2 className="bw-caps">Original message</h2>
      {docs.map((d) => {
        const body = texts[d];
        if (!body) return null;
        const spans = quoted.filter((e) => e.document_id === d).map((e) => e.span as number[])
          .sort((a, b) => a[0] - b[0]).filter((s, i, all) => i === 0 || s[0] >= all[i - 1][1]);
        const parts: (string | JSX.Element)[] = [];
        let at = 0;
        spans.forEach(([a, b], i) => { parts.push(body.slice(at, a), <mark key={i}>{body.slice(a, b)}</mark>); at = b; });
        parts.push(body.slice(at));
        return <p key={d} className="bw-quote bw-message-preview">{parts}</p>;
      })}
    </section>
  );
}

function Reply({ id, onDone, reading }: { id: number; onDone: () => Promise<unknown>; reading: boolean }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const send = async () => {
    setBusy(true);
    setErr(null);
    try {
      await guided.reply(id, { text, channel: "whatsapp" });
      setText("");
      await onDone();
    } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };
  return (
    <PinnedCard title="Got an answer?" className="bw-g1__reply" i={2}>
      <label className="bw-sr-only" htmlFor={`reply-${id}`}>The client's reply</label>
      <textarea id={`reply-${id}`} className="bw-textarea" rows={3} value={text} onChange={(e) => setText(e.target.value)}
                placeholder="Paste the client's reply. We'll read it and update what we have." />
      {err && <p role="alert" className="bw-body">{err}</p>}
      <button type="button" className="bw-btn bw-btn--soft" disabled={busy || reading || !text.trim()} onClick={send}>
        {busy || reading ? "Reading the reply…" : "Add the reply"}
      </button>
      {!text.trim() && !reading && <p className="bw-hint">Paste a reply to enable.</p>}
    </PinnedCard>
  );
}

// ---------------------------------------------------------------- the fee card (gate 2)

const OVERRIDE_REASONS = ["Round figure for the client", "Strategic client", "Market pressure", "Scope judgement", "Other"];
const REJECT_REASONS = ["Fee below what the work needs", "Client budget far below our fee", "Scope too unclear to price", "Other"];

function FeeView({ id, card, setCard, onSheet }: {
  id: number; card: FeeCard; setCard: (c: FeeCard) => void; onSheet: (s: SheetView) => void;
}) {
  const can = useCan();
  const [sheet, setSheet] = useState<"override" | "reject" | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [flip, setFlip] = useState<"opening" | "won" | null>(null);
  const [changed, setChanged] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (card.status === "missing") {
    return (
      <HeroCard label="Fee">
        <span className="bw-caps">Fee</span>
        <h2 className="bw-title">Not enough to price yet</h2>
        <p className="bw-body">The engine needs: {card.missing.map((m) => m.label).join(", ")}. Nothing is guessed where the archive cannot support an estimate.</p>
        <a className="bw-btn bw-btn--primary" href={`#/r/${id}`}>Back to what we have</a>
      </HeroCard>
    );
  }
  if (card.status === "error") return <HeroCard label="Fee"><p className="bw-body">{card.message}</p></HeroCard>;

  const cur = card.currency;
  const fmt = (n: number) => new Intl.NumberFormat(cur === "INR" ? "en-IN" : "en-US", { maximumFractionDigits: 0 }).format(n);
  const decided = card.decision;
  const agreed = card.won ? (card.won.low + card.won.high) / 2 : card.opening;
  const total = card.stages.reduce((s, x) => s + x.amount, 0) || 1;
  const engine: SourceSpec = { kind: "derived", from: "Fee engine", result: `${card.why.length} factors, ${card.assumptions.length} assumption${card.assumptions.length === 1 ? "" : "s"}` };
  const factors: Evidence[] = card.why.map((w) => ({ id: null, kind: "setting",
    why: `${w.label}: ${typeof w.value === "number" ? w.value.toLocaleString("en-US") : w.value}. ${w.source}` }));
  const openEngine = () => (isDesktop() ? setFlip(flip === "opening" ? null : "opening")
    : onSheet({ title: "How the fee is built", evidence: factors }));

  const decide = async (decision: "approve" | "override" | "reject", amount?: number, reason?: string) => {
    setBusy(true);
    setErr(null);
    try {
      const next = await guided.decideFee(id, { decision, amount, reason });
      setSheet(null);
      if (decision === "approve") {  // the button confirms in place, then the next fee waiting on you
        setDone(true);
        setTimeout(async () => {
          const queue = await api.inbox().catch(() => []);
          const nextId = queue.find((e) => e.envelope_id !== id && e.gate1_decision === "qualify" && !e.gate2_decision)?.envelope_id;
          if (nextId) location.hash = `#/r/${nextId}/fee`;
          else setCard(next);
        }, 600);
      } else {
        setCard(next);
        setChanged(true);
        setTimeout(() => setChanged(false), 1200);
      }
    } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };

  const box = (kind: "opening" | "won") => {
    const front = kind === "opening" ? (
      <div className="bw-fee__box" data-kind="opening" data-changed={changed || undefined}>
        <div className="bw-fee__label">Opening fee</div>
        <div className="bw-fee__num">{fmt(card.opening)}</div>
        <div className="bw-fee__cur">{cur}</div>
      </div>
    ) : (
      <div className="bw-fee__box" data-kind="agreed">
        <div className="bw-fee__label">Won bids landed</div>
        <div className="bw-fee__num bw-fee__num--range">{card.won ? `${short(card.won.low, "").trim()}–${short(card.won.high, "").trim()}` : "None yet"}</div>
        <div className="bw-fee__cur">{card.won ? cur : "no won bids to compare"}</div>
      </div>
    );
    const back = (
      <div className="bw-fee__box bw-fee__back" data-kind={kind === "opening" ? "opening" : "agreed"}>
        <div className="bw-fee__label">{kind === "opening" ? "Built from" : "Won bids"}</div>
        <ul className="bw-fee__facts">
          {(kind === "opening" ? card.why.slice(0, 4).map((w) => `${w.label}: ${typeof w.value === "number" ? w.value.toLocaleString("en-US") : w.value}`)
            : [card.won?.basis ?? "No won bids to compare yet."]).map((t) => <li key={t}>{t}</li>)}
        </ul>
        <button type="button" className="bw-linkish" onClick={() => setFlip(null)}>Back</button>
      </div>
    );
    return (
      <div className="bw-flip" data-flipped={flip === kind || undefined}>
        <div className="bw-flip__face" onClick={() => kind === "won" && isDesktop() && setFlip("won")}>{front}</div>
        <div className="bw-flip__face bw-flip__back" aria-hidden={flip !== kind}>{flip === kind && back}</div>
      </div>
    );
  };

  return (
    <div className="bw-fee-layout bw-enter">
      <div className="bw-stack" style={{ "--gap": "16px", "--i": 0 } as CSSProperties}>
        <HeroCard label="Fee">
          <div className="bw-stack" style={{ "--gap": "2px" } as CSSProperties}>
            <h2 className="bw-title">{card.title}</h2>
            <p className="bw-muted">{card.scope} · {card.stage_line}</p>
          </div>
          <div className="bw-fee__figures">{box("opening")}{box("won")}</div>
          <SourcePill source={engine} onOpen={openEngine} />
          <RangeBar lo={card.band.low} hi={card.band.high} opening={card.opening} agreed={agreed}
                    label={`Range ${money(card.band.low, cur)} to ${money(card.band.high, cur)} with the assumptions; opening ${money(card.opening, cur)}; similar won bids around ${money(agreed, cur)}`}
                    legend={[short(card.band.low, cur), card.won ? `won bids ~${short(agreed, cur)}` : `${card.assumptions.length} assumption${card.assumptions.length === 1 ? "" : "s"}`, short(card.band.high, cur)]} />
          <div className="bw-stages">
            {card.stages.map((s) => (
              <div key={s.stage} className="bw-stage" style={{ "--share": s.amount / total } as CSSProperties}>
                <div className="bw-label">{s.label}</div>
                <div className="bw-stage__value">{short(s.amount, cur)}</div>
              </div>
            ))}
          </div>
          {card.stage_options.map((o) => (
            <p key={o.stage} className="bw-muted">Adding {o.label} would add about {money(o.adds, cur)}.</p>
          ))}
          {card.checks.map((c, k) => {
            const ev = c.evidence ?? [];
            const src = ev.map((e) => sourceOf(e)).find(Boolean);
            return (
              <Flag key={k} level={c.level}
                    source={src && <SourcePill source={src} onOpen={() => onSheet({ title: "Where this came from", evidence: ev })} />}>
                {c.text}
              </Flag>
            );
          })}
          <details className="bw-details">
            <summary>How it's built</summary>
            <div className="bw-details__body bw-stack">
              {card.lines.map((l, k) => (
                <div key={k} className="bw-row"><span>{l.service} · {l.stage}</span><strong className="bw-push bw-num">{money(l.amount, cur)}</strong></div>
              ))}
              {card.why.map((w, k) => <p key={k}><strong>{w.label}:</strong> {typeof w.value === "number" ? w.value.toLocaleString("en-US") : w.value}. {w.source}</p>)}
              {card.won && <p>{card.won.basis}.</p>}
              <a className="bw-btn bw-btn--soft bw-btn--sm" href={`#/logic/r/${id}`}>See every rule this fee used</a>
            </div>
          </details>
          <details className="bw-details">
            <summary>Assumptions ({card.assumptions.length})</summary>
            <div className="bw-details__body bw-stack">
              {card.assumptions.map((a, k) => {
                const src = a.evidence.map((e) => sourceOf(e, a.display, a.estimate?.confidence ?? null)).find(Boolean) ?? null;
                return (
                  <Field key={a.field} label={a.label} status="estimated" value={a.display} range={a.range_display} source={src}
                         evidence={a.evidence} confidence={a.estimate?.confidence ?? null} i={k} envelopeId={id}
                         note={a.swing ? `Moves the fee −${short(a.swing.down, cur)} / +${short(a.swing.up, cur)}` : null}
                         onSheet={(evidence, label) => onSheet({ title: label, evidence })} />
                );
              })}
            </div>
          </details>
        </HeroCard>
        {card.comparables.items.length > 0 && (
          <section className="bw-stack" aria-label="Similar projects" style={{ "--i": 1 } as CSSProperties}>
            <h2 className="bw-caps">Similar projects</h2>
            {card.comparables.items.slice(0, 3).map((c) => (
              <div key={c.reference} className="bw-comparable">
                <span className="bw-comparable__name">{c.title.split(" - ")[0]}</span>
                <span className="bw-comparable__amount">{c.fee_in_target ? short(c.fee_in_target, card.comparables.currency) : ""}</span>
                <span className="bw-comparable__meta">{c.city} · {c.year} · {c.status}</span>
                <span className="bw-comparable__meta">{Math.round(c.size_m2).toLocaleString("en-US")} m²</span>
              </div>
            ))}
          </section>
        )}
      </div>

      <ActionBar>
        {err && <p role="alert" className="bw-body">{err}</p>}
        {!decided || decided.decision === "reject" ? (
          can("approve") ? (
            <>
              {decided && <p className="bw-muted">Rejected by {decided.decided_by}: {decided.reason}</p>}
              <button type="button" className="bw-btn bw-btn--approve bw-btn--block" data-done={done || undefined}
                      disabled={busy && !done} onClick={() => decide("approve")}>
                {done && <Check className="bw-icon" />}{done ? "Approved" : `Approve ${fmt(card.opening)}`}
              </button>
              <div className="bw-actionbar__row">
                <button type="button" className="bw-btn bw-btn--override" disabled={busy} onClick={() => setSheet("override")}>Override</button>
                <button type="button" className="bw-btn bw-btn--outline" disabled={busy || decided?.decision === "reject"} onClick={() => setSheet("reject")}>Reject</button>
              </div>
            </>
          ) : <p className="bw-muted">A director approves the fee.</p>
        ) : (
          <div className="bw-card bw-approved">
            <span className="bw-caps">{decided.decision === "override" ? "Overridden" : "Approved"}</span>
            <strong className="bw-figure">{money(Number(decided.amount), decided.currency ?? cur)}</strong>
            <span className="bw-muted">by {decided.decided_by}{decided.reason ? ` · ${decided.reason}` : ""}</span>
            {decided.decision === "override" && <span className="bw-muted">The engine suggested {money(Number(decided.engine_amount), cur)}.</span>}
            <a className="bw-btn bw-btn--primary bw-btn--block" href={`#/r/${id}/proposal`}>Go to the proposal <ArrowRight className="bw-icon" /></a>
          </div>
        )}
      </ActionBar>

      <ReasonSheet open={sheet === "override"} title="Override the fee" reasons={OVERRIDE_REASONS} cta="Confirm override"
                   amount={{ currency: cur, suggested: card.opening }} onClose={() => setSheet(null)}
                   onConfirm={(reason, note, amount) => decide("override", amount, note ? `${reason}: ${note}` : reason)}
                   footnote="The original suggestion and your reason stay with the proposal." />
      <ReasonSheet open={sheet === "reject"} title="Reject the fee" reasons={REJECT_REASONS} cta="Confirm rejection"
                   onClose={() => setSheet(null)}
                   onConfirm={(reason, note) => decide("reject", undefined, note ? `${reason}: ${note}` : reason)}
                   footnote="The original suggestion and your reason stay with the request." />
    </div>
  );
}

function ReasonSheet({ open, title, reasons, cta, onClose, onConfirm, footnote, amount }: {
  open: boolean; title: string; reasons: string[]; cta: string; onClose: () => void; footnote: string;
  onConfirm: (reason: string, note: string, amount?: number) => void;
  amount?: { currency: string; suggested: number };
}) {
  const [reason, setReason] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [value, setValue] = useState("");
  const n = Number(value.replace(/[^\d.]/g, ""));
  const ready = !!reason && (!amount || n > 0) && (reason !== "Other" || note.trim().length > 0);
  return (
    <Sheet open={open} title={title} onClose={onClose}>
      {amount && (
        <label className="bw-stack" style={{ "--gap": "6px" } as CSSProperties}>
          <span className="bw-label">New fee ({amount.currency})</span>
          <input className="bw-input bw-input--figure" inputMode="numeric" value={value} onChange={(e) => setValue(e.target.value)}
                 placeholder={new Intl.NumberFormat("en-US").format(amount.suggested)} />
          <span className="bw-hint">The engine suggested {money(amount.suggested, amount.currency)}.</span>
        </label>
      )}
      <div className="bw-stack" style={{ "--gap": "6px" } as CSSProperties}>
        <span className="bw-label">Reason</span>
        <div className="bw-chips">
          {reasons.map((r) => <button key={r} type="button" className="bw-chip" aria-pressed={reason === r} onClick={() => setReason(r)}>{r}</button>)}
        </div>
      </div>
      <label className="bw-stack" style={{ "--gap": "6px" } as CSSProperties}>
        <span className="bw-label">Note {reason === "Other" ? "" : "(optional)"}</span>
        <textarea className="bw-textarea" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <button type="button" className="bw-btn bw-btn--primary bw-btn--block" disabled={!ready}
              onClick={() => reason && onConfirm(reason, note.trim(), amount ? n : undefined)}>{cta}</button>
      <p className="bw-muted">{footnote}</p>
    </Sheet>
  );
}
