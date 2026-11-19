import { useContext, useState } from "react";
import { Download, PenLine, Sparkles } from "lucide-react";
import { ApiError, guided, type ProposalStep as Step, type Section, type TemplateKey } from "../api";
import { Markdown } from "../components/Markdown";
import { codeLabel } from "../format";
import { Session, useCan } from "../session";
import { HeroCard } from "./kit";
import { errorText, money } from "./ui";

const ORIGIN: Record<string, string> = {
  record: "From the request", engine: "Fee engine", library: "Standard text", claude: "Drafted by Claude",
  estimator: "Edited", pending: "Not written yet",
};

const TEMPLATE_HELP: Record<TemplateKey, string> = {
  full: "Nine sections: understanding, scope, deliverables, programme, fee, expenses, payment, exclusions, terms.",
  letter: "One to two pages: the fee by stage, how it is paid and on what basis. For quick asks.",
};

/** Step 4: the proposal, on screen, then exported as the full proposal or the fee letter. */
export function ProposalStep({ id, step, setStep }: { id: number; step: Step | null; setStep: (s: Step) => void }) {
  const can = useCan();
  const me = useContext(Session);
  const [template, setTemplate] = useState<TemplateKey>("full");
  const [busy, setBusy] = useState<"draft" | "export" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);

  if (!step) return <p className="bw-muted">Loading…</p>;
  const p = step.proposal;
  if (!p) {
    return (
      <HeroCard label="Proposal">
        <span className="bw-caps">Proposal</span>
        <h2 className="bw-title">Approve the fee first</h2>
        <p className="bw-body">The proposal is created from the approved fee, so its figures always match what the director signed off.</p>
        <a className="bw-btn bw-btn--primary" href={`#/r/${id}/fee`}>Go to the fee</a>
      </HeroCard>
    );
  }

  const run = async (what: "draft" | "export") => {
    setBusy(what);
    setErr(null);
    setProblems([]);
    try {
      setStep(what === "draft" ? await guided.draftProposal(id) : await guided.exportProposal(id, template));
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail as { problems?: string[] } : null;
      if (detail?.problems) setProblems(detail.problems);
      setErr(errorText(e));
    } finally { setBusy(null); }
  };

  const sections = Object.fromEntries(p.sections.map((s) => [s.code, s])) as Record<string, Section>;
  const cover = sections.cover?.kind === "cover" ? sections.cover.data : null;
  const cur = p.currency;
  const files = p.outputs_by_template[template] ?? [];
  const written = p.pending.length === 0;

  return (
    <div className="bw-fee-layout">
      <div className="bw-stack" style={{ "--gap": "16px" } as React.CSSProperties}>
        {!written && (
          <HeroCard label="Write the proposal">
            <span className="bw-caps">Write the proposal</span>
            <h2 className="bw-title">The figures are in; the words are next</h2>
            <p className="bw-body">Claude writes the project understanding and the client-specific scope and terms from the request,
              citing where each point came from. Standard scope, exclusions and terms come from the practice's own library. About 40 seconds.</p>
            {can("approve") ? (
              <button type="button" className="bw-btn bw-btn--primary bw-btn--block" disabled={!!busy} onClick={() => run("draft")}>
                <Sparkles className="bw-icon" />{busy === "draft" ? "Writing the proposal…" : "Write the proposal"}
              </button>
            ) : <p className="bw-muted">A director writes and sends the proposal.</p>}
          </HeroCard>
        )}

        <div className="bw-segmented" role="group" aria-label="Template">
          {step.templates.map((t) => (
            <button key={t.key} type="button" aria-pressed={template === t.key} onClick={() => setTemplate(t.key)}>{t.name}</button>
          ))}
        </div>
        <p className="bw-muted">{TEMPLATE_HELP[template]}</p>

        <DocPage hero={written}>
          <header className="bw-doc__cover">
            <span className="bw-caps">{me?.workspace?.name} · {p.reference}</span>
            <h2 className="bw-title">{template === "letter" ? `Fee proposal: ${cover?.project ?? p.title}` : cover?.project ?? p.title}</h2>
            <dl className="bw-doc__facts">
              {cover?.client && <><dt>Prepared for</dt><dd>{cover.client}</dd></>}
              {cover?.location && <><dt>Location</dt><dd>{cover.location}</dd></>}
              <dt>Date</dt><dd>{new Date(cover?.date ?? p.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}</dd>
              <dt>Valid for</dt><dd>{cover?.validity_days ?? 90} days</dd>
              <dt>Fee</dt><dd><strong>{money(Number(p.fee_total_local), cur)}</strong></dd>
            </dl>
          </header>
          {template === "letter" ? (
            <>
              <p className="bw-body">Thank you for your request. We are pleased to set out our fee below. Our reference is {p.reference}.</p>
              <FeeTable p={p} />
              <PaymentTable s={sections.payment} cur={cur} />
              <ul className="bw-doc__list">
                <li>This fee is valid for {cover?.validity_days ?? 90} days from the date of this letter.</li>
                <li>All fees are in {cur} and exclude VAT or other applicable taxes.</li>
                <li>Reimbursable expenses, exclusions and our standard terms are as set out in our full proposal.</li>
              </ul>
            </>
          ) : p.sections.filter((s) => s.code !== "cover").map((s, i) => (
            <section key={s.code} className="bw-doc__section">
              <div className="bw-row">
                <h3 className="bw-doc__h">{i + 1}. {s.title}</h3>
                <span className="bw-badge bw-push" data-status={s.origin === "pending" ? "missing" : s.origin === "claude" ? "estimated" : "found"}>
                  {ORIGIN[s.origin] ?? s.origin}
                </span>
              </div>
              <SectionBody s={s} p={p} />
            </section>
          ))}
        </DocPage>
      </div>

      <div className="bw-actionbar">
        {err && <p role="alert" className="bw-body">{err}</p>}
        {problems.length > 0 && <ul className="bw-doc__list">{problems.map((x) => <li key={x}>{x}</li>)}</ul>}
        {files.length > 0 ? (
          <>
            <span className="bw-caps">Exported · {step.templates.find((t) => t.key === template)?.name}</span>
            {files.map((o) => (
              <a key={o.output_id} className="bw-btn bw-btn--soft bw-btn--block" href={`/api/outputs/${o.output_id}`}>
                <Download className="bw-icon" />Download {o.format === "pdf" ? "PDF" : "Word"}
              </a>
            ))}
          </>
        ) : can("approve") && (
          <button type="button" className="bw-btn bw-btn--primary bw-btn--block" disabled={!!busy || !written} onClick={() => run("export")}>
            <Download className="bw-icon" />{busy === "export" ? "Exporting…" : `Export ${template === "full" ? "the proposal" : "the fee letter"}`}
          </button>
        )}
        {!written && <p className="bw-muted">Export unlocks once the proposal is written.</p>}
        <a className="bw-btn bw-btn--soft bw-btn--block" href={`#/proposal/${p.proposal_id}/v/${p.version_no}`}>
          <PenLine className="bw-icon" />Edit sections
        </a>
        <p className="bw-muted">Exporting approves this version: it is frozen, and changes make a new version.</p>
      </div>
    </div>
  );
}

type P = NonNullable<Step["proposal"]>;

function FeeTable({ p }: { p: P }) {
  return (
    <table className="bw-table">
      <thead><tr><th>Service</th><th>Stage</th><th className="num">Fee ({p.currency})</th></tr></thead>
      <tbody>
        {p.fee_lines.map((l) => (
          <tr key={l.line_no}><td>{codeLabel(l.service)}</td><td>{codeLabel(l.stage)}</td>
            <td className="num">{Number(l.amount_local).toLocaleString("en-US")}</td></tr>
        ))}
        <tr className="bw-table__total"><td /><td>Total professional fee</td><td className="num">{Number(p.fee_total_local).toLocaleString("en-US")}</td></tr>
      </tbody>
    </table>
  );
}

function PaymentTable({ s, cur }: { s?: Section; cur: string }) {
  if (!s || s.code !== "payment" || !s.data?.length) return null;
  return (
    <table className="bw-table">
      <thead><tr><th>No.</th><th>Milestone</th><th className="num">Share</th><th className="num">Amount ({cur})</th></tr></thead>
      <tbody>{s.data.map((m) => (
        <tr key={m.seq}><td>{m.seq}</td><td>{m.milestone}</td><td className="num">{m.pct}%</td>
          <td className="num">{Number(m.amount_local).toLocaleString("en-US")}</td></tr>
      ))}</tbody>
    </table>
  );
}

function SectionBody({ s, p }: { s: Section; p: P }) {
  if (s.kind === "prose") {
    return s.body ? <Markdown text={s.body} /> : <p className="bw-muted">Written in the next step.</p>;
  }
  if (s.code === "fees") return <FeeTable p={p} />;
  if (s.code === "payment") return <PaymentTable s={s} cur={p.currency} />;
  if (s.code === "deliverables") {
    return <ul className="bw-doc__list">{(s.data ?? []).map((d, i) => <li key={i}>{d.item}: {d.qty}</li>)}</ul>;
  }
  if (s.code === "programme") {
    return (
      <table className="bw-table"><tbody>{(s.data ?? []).map((r) => (
        <tr key={r.stage}><td>{r.label}</td><td className="num">{r.duration}</td></tr>
      ))}</tbody></table>
    );
  }
  if (s.code === "reimbursables") {
    return (
      <table className="bw-table">
        <thead><tr><th>Item</th><th className="num">Qty</th><th className="num">Amount ({p.currency})</th></tr></thead>
        <tbody>{(s.data ?? []).map((r, i) => (
          <tr key={i}><td>{codeLabel(r.type)}</td><td className="num">{r.qty}</td>
            <td className="num">{Number(r.amount_local).toLocaleString("en-US")}</td></tr>
        ))}</tbody>
      </table>
    );
  }
  return null;
}

/** The proposal as a page: the view's hero once its words are written, a pinned card until then. */
function DocPage({ hero, children }: { hero: boolean; children: React.ReactNode }) {
  return hero ? <HeroCard label="Proposal preview" className="bw-doc">{children}</HeroCard>
    : <article className="bw-card bw-doc" aria-label="Proposal preview">{children}</article>;
}
