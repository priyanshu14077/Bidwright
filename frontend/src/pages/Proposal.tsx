import { useCallback, useEffect, useState } from "react";
import {
  ApiError, api, proposals, type CitedSource, type Doc, type DraftReport, type FeeLine, type PricingChoices,
  type Proposal as ProposalT, type ProposalEvent, type Reference, type Section, type Version,
} from "../api";
import { PdfViewer, type Highlight } from "../components/PdfViewer";
import { Markdown } from "../components/Markdown";
import { InlineEdit, PricingForm } from "../components/Edit";
import {
  DOC_CLASS, SECTION_ORIGIN, VERSION_STATUS, codeLabel, errorText, formatDate, money, shortMoney,
} from "../format";
import { useCan } from "../session";

type Tab = "document" | "fees" | "history";
type Banner = { tone: "error" | "info"; text: string } | null;
const PROSE = ["understanding", "scope", "exclusions", "terms"] as const;

const go = (hash: string) => { location.hash = hash; };
const versionHash = (p: number, n: number) => `#/proposal/${p}/v/${n}`;

export function ProposalPage({ proposalId, versionNo, reference }:
  { proposalId: number; versionNo: number; reference: Reference | null }) {
  const [v, setV] = useState<Version | null>(null);
  const [proposal, setProposal] = useState<ProposalT | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [docId, setDocId] = useState<number | null>(null);
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [tab, setTab] = useState<Tab>("document");
  const [banner, setBanner] = useState<Banner>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const can = useCan();

  const load = useCallback(async () => {
    try {
      const [ver, prop] = await Promise.all([proposals.version(proposalId, versionNo), proposals.get(proposalId)]);
      setV(ver);
      setProposal(prop);
      return ver;
    } catch (e) { setBanner({ tone: "error", text: errorText(e) }); return null; }
  }, [proposalId, versionNo]);

  useEffect(() => {
    load().then((ver) => ver && api.detail(ver.envelope_id).then((d) => {
      setDocs(d.documents);
      setDocId(d.documents.find((x) => x.doc_class === "main_rfp")?.document_id
        ?? d.documents.find((x) => x.has_pdf)?.document_id ?? null);
    }, () => setDocs([])));
  }, [load]);

  /** Run a change. The server returns the whole version, so the page shows exactly what was saved. */
  const act = useCallback(async (label: string, run: (revision: number) => Promise<Version>): Promise<boolean> => {
    if (!v) return false;
    setBusy(label);
    setBanner(null);
    try {
      setV(await run(v.revision));
      proposals.get(proposalId).then(setProposal);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && hasRevision(e.detail)) {
        setBanner({ tone: "error", text: "Someone else changed this version while you were working. It has been reloaded; "
          + "your unsaved text is still in the editor." });
        await load();
        return false;
      }
      throw e;
    } finally { setBusy(null); }
  }, [v, proposalId, load]);

  const cite = (s: CitedSource) => {
    if (s.kind !== "rfp" || !s.document_id || !s.page || !s.rects?.length) return;
    setDocId(s.document_id);
    setHighlight({ documentId: s.document_id, page: s.page, rects: s.rects, tone: "source" });
  };

  if (!v) return <div className="page">{banner ? <p className="error">{banner.text}</p> : <p className="empty">Loading…</p>}</div>;
  const latest = proposal ? Math.max(...proposal.versions.map((x) => x.version_no)) : v.version_no;
  const editable = v.status === "draft" && can("review") && !busy;
  const doc = docs.find((d) => d.document_id === docId);

  return (
    <div className="review proposal">
      <TitleBlock v={v} latest={latest} busy={busy} act={act} onBanner={setBanner} reference={reference} />
      {banner && <p className={`banner banner-${banner.tone}`} role="alert">{banner.text}</p>}
      {v.stale && v.status === "draft" && (
        <p className="banner banner-error">The request was confirmed again after this version was priced. Reprice it before approval.</p>
      )}
      {busy && <p className="banner banner-info" role="status">{busy}</p>}
      <div className="split">
        <section className="sheets" aria-label="RFP documents">
          <ol className="register">
            {docs.map((d) => (
              <li key={d.document_id}>
                <button className={d.document_id === docId ? "on" : ""} disabled={!d.has_pdf} onClick={() => setDocId(d.document_id)}>
                  <span className="register-name">{d.file_name}</span>
                  <span className="register-meta">{DOC_CLASS[d.doc_class ?? ""] ?? "Unclassified"}</span>
                </button>
              </li>
            ))}
          </ol>
          <div className="sheet-scroll">
            {doc?.has_pdf
              ? <PdfViewer url={api.pdfUrl(doc.document_id)} documentId={doc.document_id} highlight={highlight} width={600} />
              : <p className="empty">{docs.length ? "Select a document." : "No RFP documents for this request."}</p>}
          </div>
        </section>
        <section className="panel">
          <nav className="tabs" role="tablist">
            {([["document", "Document"], ["fees", "Fees"], ["history", "History"]] as const).map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
            ))}
          </nav>
          <div className="panel-scroll">
            {tab === "document" && <DocumentTab v={v} editable={editable} act={act} onCite={cite} onFees={() => setTab("fees")} />}
            {tab === "fees" && <FeesTab v={v} editable={editable} act={act} reference={reference} />}
            {tab === "history" && proposal && <HistoryTab proposal={proposal} current={v.version_no} />}
          </div>
        </section>
      </div>
    </div>
  );
}

type Act = (label: string, run: (revision: number) => Promise<Version>) => Promise<boolean>;

function hasRevision(detail: unknown): boolean {
  return typeof detail === "object" && detail !== null && "current_revision" in detail;
}

function problemsOf(e: unknown): string[] | null {
  if (!(e instanceof ApiError) || typeof e.detail !== "object" || e.detail === null || !("problems" in e.detail)) return null;
  const { problems } = e.detail;
  return Array.isArray(problems) ? problems.map(String) : null;
}

// ---------------------------------------------------------------- title block and actions

function TitleBlock({ v, latest, busy, act, onBanner, reference }: {
  v: Version; latest: number; busy: string | null; act: Act; onBanner: (b: Banner) => void; reference: Reference | null;
}) {
  const can = useCan();
  const [panel, setPanel] = useState<"draft" | "reprice" | "approve" | null>(null);
  const cover = v.sections.find((s): s is Extract<Section, { code: "cover" }> => s.code === "cover");
  const pending = v.sections.filter((s) => s.kind === "prose" && s.origin === "pending").map((s) => s.code);
  const diff = v.fee_total_local - v.engine_total_local;
  const run = (label: string, fn: (rev: number) => Promise<Version>) =>
    act(label, fn).catch((e) => { onBanner({ tone: "error", text: errorText(e) }); return false; });

  return (
    <header className="titleblock">
      <div className="tb-cell tb-project">
        <a className="link" href={`#/rfp/${v.envelope_id}`}>Request</a>
        <h1>{v.title}</h1>
        <p>{cover?.data.client ?? ""}{cover?.data.location ? `, ${cover.data.location}` : ""}</p>
      </div>
      <div className="tb-cell">
        <span className="tb-label">Proposal</span>
        <span><strong>{v.reference}</strong> version {v.version_no}</span>
        <span className={`stamp-sm status-${v.status}`}>{VERSION_STATUS[v.status]}</span>
        <span className="tb-sub">priced on record v{v.record_version}{v.status === "draft" ? `, revision ${v.revision}` : ""}</span>
      </div>
      <div className="tb-cell">
        <span className="tb-label">Professional fee</span>
        <span className="tb-figure">{money(v.fee_total_local, v.currency)}</span>
        <span className="tb-sub">{Math.abs(diff) < 1 ? "as the fee engine priced it"
          : `${diff > 0 ? "+" : "−"}${money(Math.abs(diff), v.currency)} against the engine`}</span>
        <span className="tb-sub">plus reimbursables {money(v.reimbursables_total_local, v.currency)}</span>
      </div>
      <div className="tb-cell">
        <span className="tb-label">Approval</span>
        {v.status === "draft" ? <span className="tb-sub">Needs an admin (gate 3). Approval freezes this version.</span>
          : <span className="tb-sub">{v.approved_by}, {v.approved_at ? formatDate(v.approved_at) : ""}</span>}
        {v.status !== "draft" && v.version_no < latest && (
          <a className="link" href={versionHash(v.proposal_id, latest)}>Go to version {latest}</a>)}
        {v.status !== "draft" && <Exports v={v} onDone={() => act("Reloading…", () => proposals.version(v.proposal_id, v.version_no))}
                                          onBanner={onBanner} />}
      </div>
      <div className="tb-cell tb-actions">
        {v.status === "draft" && can("review") && <>
          <button className="primary" disabled={!!busy} onClick={() => setPanel(panel === "draft" ? null : "draft")}>
            {pending.length ? `Draft ${pending.length} section${pending.length > 1 ? "s" : ""} with Claude` : "Redraft with Claude"}
          </button>
          <button disabled={!!busy} onClick={() => setPanel(panel === "reprice" ? null : "reprice")}>Reprice</button>
        </>}
        {v.status === "draft" && can("approve") && (
          <button disabled={!!busy} onClick={() => setPanel(panel === "approve" ? null : "approve")}>Approve…</button>)}
        {v.status !== "draft" && v.version_no === latest && can("review") && (
          <button className="primary" disabled={!!busy} onClick={async () => {
            try { const nv = await proposals.newVersion(v.proposal_id, "revise the approved proposal"); go(versionHash(nv.proposal_id, nv.version_no)); }
            catch (e) { onBanner({ tone: "error", text: errorText(e) }); }
          }}>New version</button>)}
        {!can("review") && <span className="tb-sub">Your role can read this proposal but not change it.</span>}
      </div>
      {panel === "draft" && <DraftPanel pending={pending} onClose={() => setPanel(null)}
        onRun={(sections) => run("Drafting with Claude. This takes about half a minute…",
          (rev) => proposals.draft(v.proposal_id, v.version_no, { revision: rev, sections })).then((ok) => ok && setPanel(null))} />}
      {panel === "reprice" && <RepricePanel initial={v.pricing_choices} onClose={() => setPanel(null)}
        onRun={(choices, reason) => run("Repricing…",
          (rev) => proposals.reprice(v.proposal_id, v.version_no, { ...choices, revision: rev, reason }))
          .then((ok) => ok && setPanel(null))} />}
      {panel === "approve" && <ApprovePanel act={act} v={v} onClose={() => setPanel(null)} />}
    </header>
  );
}

function DraftPanel({ pending, onRun, onClose }:
  { pending: string[]; onRun: (sections: string[]) => void; onClose: () => void }) {
  const [chosen, setChosen] = useState<string[]>(pending.length ? pending : ["understanding"]);
  return (
    <div className="tb-panel">
      <p>Claude writes the Project Understanding, scope the RFP asks for beyond your standard text, and the client's
        requested changes to your terms, citing the record and RFP lines. Exclusions and standard text need no model.
        Redrafting replaces any edits in the chosen sections.</p>
      <fieldset className="checks"><legend>Sections</legend>
        {PROSE.map((c) => (
          <label key={c}><input type="checkbox" checked={chosen.includes(c)}
            onChange={(e) => setChosen(e.target.checked ? [...chosen, c] : chosen.filter((x) => x !== c))} />
            {codeLabel(c)}{pending.includes(c) ? " (not written yet)" : ""}</label>
        ))}
      </fieldset>
      <div className="row">
        <button className="primary" disabled={!chosen.length} onClick={() => onRun(chosen)}>Draft</button>
        <button onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

function RepricePanel({ initial, onRun, onClose }:
  { initial: PricingChoices; onRun: (c: PricingChoices, reason: string) => void; onClose: () => void }) {
  const [choices, setChoices] = useState<PricingChoices>(initial);
  const [reason, setReason] = useState("");
  return (
    <div className="tb-panel">
      <p>Prices again from the latest confirmed record and today's settings. <strong>Every fee line is replaced</strong>,
        including lines you edited; sections you edited are kept.</p>
      <PricingForm value={choices} onChange={setChoices} />
      <label>Reason (required)<input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <div className="row">
        <button className="primary" disabled={reason.trim().length < 3} onClick={() => onRun(choices, reason)}>Reprice</button>
        <button onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

function ApprovePanel({ v, act, onClose }: { v: Version; act: Act; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [problems, setProblems] = useState<string[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const approve = async () => {
    setProblems(null); setErr(null);
    try {
      if (await act("Approving…", (rev) => proposals.approve(v.proposal_id, v.version_no, rev, reason || undefined))) onClose();
    } catch (e) {
      const p = problemsOf(e);
      if (p) setProblems(p); else setErr(errorText(e));
    }
  };
  return (
    <div className="tb-panel">
      <p>Approving freezes version {v.version_no}: nothing in it can change afterwards, and it becomes the version to
        export. Any earlier approved version is marked superseded.</p>
      <label>Note (optional)<input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <div className="row">
        <button className="primary" onClick={approve}>Approve version {v.version_no}</button>
        <button onClick={onClose}>Cancel</button>
      </div>
      {problems && <div className="why"><h2>Not ready to approve</h2><ul>{problems.map((p) => <li key={p} className="sev-block">{p}</li>)}</ul></div>}
      {err && <p className="error">{err}</p>}
    </div>
  );
}

/** Approved versions export to Word and PDF; anyone who can read the proposal can download the files. */
function Exports({ v, onDone, onBanner }: { v: Version; onDone: () => void; onBanner: (b: Banner) => void }) {
  const can = useCan();
  const [working, setWorking] = useState(false);
  const latest = ["docx", "pdf"].map((f) => v.outputs.find((o) => o.format === f)).filter((o) => o !== undefined);
  const run = async () => {
    setWorking(true);
    try { await proposals.exportFiles(v.proposal_id, v.version_no); onDone(); }
    catch (e) { onBanner({ tone: "error", text: errorText(e) }); }
    finally { setWorking(false); }
  };
  return (
    <div className="exports">
      {latest.map((o) => (
        <a key={o.output_id} className="download" href={proposals.downloadUrl(o.output_id)} download={o.file_name}>
          {o.format.toUpperCase()} <span className="dim">{Math.round(o.size_bytes / 1024)} KB</span></a>))}
      {can("approve") && (
        <button disabled={working} onClick={run}>{working ? "Exporting…" : latest.length ? "Export again" : "Export Word and PDF"}</button>)}
      {!latest.length && !can("approve") && <span className="tb-sub">Not exported yet. An admin exports approved versions.</span>}
    </div>
  );
}

// ---------------------------------------------------------------- document

function DocumentTab({ v, editable, act, onCite, onFees }:
  { v: Version; editable: boolean; act: Act; onCite: (s: CitedSource) => void; onFees: () => void }) {
  const lastDraft = [...v.events].reverse().find((e) => e.action === "draft");
  const report = lastDraft && isDraftReport(lastDraft.new) ? lastDraft.new : null;
  return (
    <div className="proposal-doc">
      {report && <DraftNotes report={report} at={lastDraft?.at ?? ""} />}
      {v.sections.map((s) => (
        <SectionCard key={s.code} s={s} v={v} editable={editable} act={act} onCite={onCite} onFees={onFees} />
      ))}
    </div>
  );
}

function isDraftReport(x: unknown): x is DraftReport {
  return typeof x === "object" && x !== null && "notes" in x && Array.isArray(x.notes)
    && "warnings" in x && Array.isArray(x.warnings);
}

function DraftNotes({ report, at }: { report: DraftReport; at: string }) {
  const [open, setOpen] = useState(true);
  if (!report.notes.length && !report.warnings.length) return null;
  return (
    <aside className="notes">
      <button className="link" onClick={() => setOpen(!open)}>
        Notes for the estimator from the last draft ({report.notes.length + report.warnings.length}){open ? "" : ", show"}
      </button>
      {open && <>
        <ul>
          {report.warnings.map((w) => <li key={w} className="sev-warn">{w}</li>)}
          {report.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
        <p className="dim">{formatDate(at)}{report.llm_call ? `, ${report.llm_call.model}, ${report.llm_call.input_tokens.toLocaleString("en-GB")} tokens in, `
          + `${report.llm_call.output_tokens.toLocaleString("en-GB")} out, $${Number(report.llm_call.cost_usd ?? 0).toFixed(3)}` : ""}
          {report.attempts > 1 ? `, accepted on attempt ${report.attempts}` : ""}</p>
      </>}
    </aside>
  );
}

function SectionCard({ s, v, editable, act, onCite, onFees }: {
  s: Section; v: Version; editable: boolean; act: Act; onCite: (c: CitedSource) => void; onFees: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(s.body ?? "");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setErr(null);
    try {
      if (await act("Saving…", (rev) => proposals.editSection(v.proposal_id, v.version_no, s.code, { revision: rev, body: text, reason }))) {
        setEditing(false);
      }
    } catch (e) { setErr(errorText(e)); }
  };
  return (
    <section className={`doc-section origin-${s.origin}`} id={`section-${s.code}`}>
      <header className="doc-section-head">
        <h2>{s.code === "cover" ? "Cover" : `${s.seq}. ${s.title}`}</h2>
        <span className={`badge badge-${s.origin}`}>{SECTION_ORIGIN[s.origin]}</span>
        {s.updated_by && s.origin === "estimator" && <span className="dim">{s.updated_by}</span>}
        {editable && s.kind === "prose" && !editing && (
          <button className="edit-link" onClick={() => { setText(s.body ?? ""); setReason(""); setEditing(true); }}>Edit</button>)}
      </header>
      {editing ? (
        <div className="editor">
          <textarea rows={Math.min(24, Math.max(6, text.split("\n").length + 2))} value={text} onChange={(e) => setText(e.target.value)} />
          <p className="dim">Markdown: ### heading, **bold**, "- " bullets, | pipe | tables |. Leave [to be written] where something is still missing; approval refuses it.</p>
          <label>Reason (required)<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. tightened the client's brief" /></label>
          <div className="row">
            <button className="primary" disabled={reason.trim().length < 3 || !text.trim()} onClick={save}>Save section</button>
            <button onClick={() => setEditing(false)}>Cancel</button>
          </div>
          {err && <p className="error">{err}</p>}
        </div>
      ) : <SectionBody s={s} v={v} onFees={onFees} />}
      {!editing && s.citations.length > 0 && <Citations citations={s.citations} onCite={onCite} />}
    </section>
  );
}

function SectionBody({ s, v, onFees }: { s: Section; v: Version; onFees: () => void }) {
  switch (s.code) {
    case "cover": {
      const c = s.data;
      const rows: [string, string][] = [["Client", c.client ?? ""], ["Project", c.project ?? ""], ["Location", c.location],
        ["Our reference", c.reference], ["Client reference", c.client_reference ?? ""], ["Date", formatDate(c.date)],
        ["Valid for", `${c.validity_days} days`], ["Currency", c.currency]];
      return <dl className="cover">{rows.map(([k, val]) => <div key={k} className="dl-row"><dt>{k}</dt><dd>{val}</dd></div>)}</dl>;
    }
    case "understanding": case "scope": case "exclusions": case "terms":
      return s.body ? <Markdown text={s.body} />
        : <p className="empty">Not written yet. Draft it with Claude, or edit it to write it yourself.</p>;
    case "deliverables":
      return <SimpleTable head={["Deliverable", "Quantity"]} rows={s.data.map((d) => [d.item, String(d.qty)])} />;
    case "programme":
      return <SimpleTable head={["Stage", "Indicative duration"]} rows={s.data.map((p) => [p.label, p.duration])} />;
    case "reimbursables":
      return <SimpleTable head={["Item", "Qty", `Rate (${v.currency})`, `Amount (${v.currency})`]} num={[1, 2, 3]}
        rows={s.data.map((r) => [`${codeLabel(r.type)}: ${r.note}`, String(r.qty), money(r.unit_rate_local, ""), money(r.amount_local, "")])} />;
    case "payment":
      return <SimpleTable head={["No.", "Milestone", "Share", `Amount (${v.currency})`]} num={[2, 3]}
        rows={s.data.map((m) => [String(m.seq), m.milestone, `${m.pct}%`, money(m.amount_local, "")])} />;
    case "fees":
      return (
        <>
          <SimpleTable head={["Service", "Stage", `Fee (${v.currency})`]} num={[2]}
            rows={[...v.fee_lines.map((l) => [codeLabel(l.service), codeLabel(l.stage), money(l.amount_local, "")]),
              ["", "Total professional fee", money(v.fee_total_local, "")]]} />
          <button className="link" onClick={onFees}>Edit fees, see how each line was priced</button>
        </>
      );
    default: {
      const _exhaustive: never = s;
      return _exhaustive;
    }
  }
}

function SimpleTable({ head, rows, num = [] }: { head: string[]; rows: string[][]; num?: number[] }) {
  return (
    <table>
      <thead><tr>{head.map((h, i) => <th key={h} className={num.includes(i) ? "num" : undefined}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={num.includes(j) ? "num" : undefined}>{c}</td>)}</tr>)}</tbody>
    </table>
  );
}

const SOURCE_KIND: Record<CitedSource["kind"], string> = { record: "Record", rfp: "RFP", past_proposal: "Past proposal" };

function Citations({ citations, onCite }: { citations: { part: string; sources: CitedSource[] }[]; onCite: (s: CitedSource) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="citations">
      <span className="dim">Sources</span>
      {citations.map((c) => (
        <div key={c.part} className="citation-part">
          <span className="dim">{c.part}</span>
          {c.sources.map((s) => (
            <button key={s.id} className={`chip chip-${s.kind}${open === `${c.part}:${s.id}` ? " on" : ""}`}
                    title={s.quote ?? s.id}
                    onClick={() => { setOpen(open === `${c.part}:${s.id}` ? null : `${c.part}:${s.id}`); onCite(s); }}>
              {s.kind === "rfp" ? `${s.document ?? "RFP"} p.${s.page ?? "?"}` : s.kind === "record" ? codeLabel(s.id.slice(7)) : s.id.slice(5)}
            </button>
          ))}
          {c.sources.filter((s) => open === `${c.part}:${s.id}`).map((s) => (
            <p key={s.id} className="quote"><strong>{SOURCE_KIND[s.kind]}</strong> {s.quote ?? "(no text)"}
              {s.kind === "rfp" && !s.rects?.length && <span className="dim"> (position not recorded; draft again to locate it)</span>}</p>
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- fees

function FeesTab({ v, editable, act, reference }: { v: Version; editable: boolean; act: Act; reference: Reference | null }) {
  const [open, setOpen] = useState<number | null>(null);
  const est = v.estimate;
  const payment = v.sections.find((s): s is Extract<Section, { code: "payment" }> => s.code === "payment");
  const reimb = v.sections.find((s): s is Extract<Section, { code: "reimbursables" }> => s.code === "reimbursables");
  return (
    <div className="fees">
      <table className="fee-table">
        <thead><tr><th>Service</th><th>Stage</th><th>Basis</th><th className="num">Engine</th><th className="num">Fee ({v.currency})</th><th /></tr></thead>
        <tbody>
          {v.fee_lines.map((l) => (
            <LineRow key={l.line_no} l={l} v={v} editable={editable} act={act} open={open === l.line_no}
                     onToggle={() => setOpen(open === l.line_no ? null : l.line_no)} />
          ))}
          <tr className="totals">
            <td colSpan={3}>Total professional fee</td>
            <td className="num">{money(v.engine_total_local, "")}</td>
            <td className="num">{money(v.fee_total_local, "")}</td><td />
          </tr>
        </tbody>
      </table>
      {editable && <AddLine v={v} act={act} reference={reference} />}

      <div className="cols fee-context">
        <section className="group">
          <h2>How the engine priced it</h2>
          {est.range && <p>Won bids priced this way came in at <strong>{shortMoney(est.range.low_local, v.currency)} to {shortMoney(est.range.high_local, v.currency)}</strong>. <span className="dim">{est.range.basis}</span></p>}
          <dl>{est.factors.map((f) => <div key={f.name} className="dl-row"><dt>{codeLabel(f.name)}</dt><dd>{String(f.value)}<br /><span className="dim">{f.source}</span></dd></div>)}</dl>
          {est.inputs.assumed.length > 0 && <><h2 className="spaced">Assumed by the engine</h2><ul>{est.inputs.assumed.map((a) => <li key={a}>{a}</li>)}</ul></>}
          {est.warnings.length > 0 && <><h2 className="spaced">Not priced</h2><ul>{est.warnings.map((w) => <li key={w} className="sev-warn">{w}</li>)}</ul></>}
          <p className="dim">{est.engine_version}; choices: {Object.keys(v.pricing_choices).length ? JSON.stringify(v.pricing_choices) : "engine defaults"}</p>
        </section>
        <section className="group">
          <h2>Payment schedule</h2>
          {payment && <SimpleTable head={["Milestone", "Share", `Amount (${v.currency})`]} num={[1, 2]}
            rows={payment.data.map((m) => [m.milestone, `${m.pct}%`, money(m.amount_local, "")])} />}
          <h2 className="spaced">Reimbursables</h2>
          {reimb && <SimpleTable head={["Item", "Qty", `Amount (${v.currency})`]} num={[1, 2]}
            rows={reimb.data.map((r) => [codeLabel(r.type), String(r.qty), money(r.amount_local, "")])} />}
        </section>
      </div>
    </div>
  );
}

function LineRow({ l, v, editable, act, open, onToggle }:
  { l: FeeLine; v: Version; editable: boolean; act: Act; open: boolean; onToggle: () => void }) {
  const [removing, setRemoving] = useState(false);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const diff = l.engine_amount_local == null ? null : l.amount_local - l.engine_amount_local;
  const edited = diff !== null && Math.abs(diff) >= 1;
  return (
    <>
      <tr className={edited || l.engine_amount_local == null ? "edited" : undefined}>
        <td><button className="link" onClick={onToggle} aria-expanded={open}>{codeLabel(l.service)}</button></td>
        <td>{codeLabel(l.stage)}</td>
        <td className="dim">{l.basis === "estimator" ? "added by hand" : `${Number(l.basis_qty ?? 0).toLocaleString("en-GB")} ${l.basis_unit ?? ""}`}</td>
        <td className="num dim">{l.engine_amount_local == null ? "—" : money(l.engine_amount_local, "")}</td>
        <td className="num">
          <InlineEdit display={<>{money(l.amount_local, "")}{edited && diff !== null && <span className={diff > 0 ? "diff-up" : "diff-down"}>
            {diff > 0 ? " +" : " −"}{money(Math.abs(diff), "")}</span>}</>}
            initial={String(Math.round(l.amount_local))} input="number" canEdit={editable}
            onSave={(val, why) => act("Saving…", (rev) => proposals.editLine(v.proposal_id, v.version_no, l.line_no,
              { revision: rev, amount_local: Number(val), reason: why }))} />
        </td>
        <td>{editable && <button className="edit-link" onClick={() => setRemoving(!removing)}>Remove</button>}</td>
      </tr>
      {(open || l.note) && (
        <tr className="line-detail"><td colSpan={6}>
          {l.note && <p><strong>Note:</strong> {l.note}</p>}
          {open && (l.factors.length ? <ul>{l.factors.map((f) => <li key={f.name}><strong>{codeLabel(f.name)}</strong> {String(f.value)} <span className="dim">{f.source}</span></li>)}</ul>
            : <p className="dim">Added by hand; no engine factors.</p>)}
        </td></tr>
      )}
      {removing && (
        <tr className="line-detail"><td colSpan={6}>
          <div className="row">
            <input placeholder="Reason for removing this line (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <button className="primary" disabled={reason.trim().length < 3} onClick={() =>
              act("Removing…", (rev) => proposals.deleteLine(v.proposal_id, v.version_no, l.line_no, rev, reason))
                .catch((e) => { setErr(errorText(e)); return false; })}>Remove line</button>
            <button onClick={() => setRemoving(false)}>Cancel</button>
          </div>
          {err && <p className="error">{err}</p>}
        </td></tr>
      )}
    </>
  );
}

function AddLine({ v, act, reference }: { v: Version; act: Act; reference: Reference | null }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ service: "", stage: "", amount: "", note: "" });
  const [err, setErr] = useState<string | null>(null);
  if (!open) return <button className="add" onClick={() => setOpen(true)}>Add a line the engine did not price</button>;
  const ok = form.service && form.stage && Number(form.amount) > 0 && form.note.trim().length >= 3;
  const add = async () => {
    setErr(null);
    try {
      if (await act("Adding…", (rev) => proposals.addLine(v.proposal_id, v.version_no, { revision: rev, service: form.service,
        stage: form.stage, amount_local: Number(form.amount), note: form.note, reason: form.note }))) {
        setOpen(false); setForm({ service: "", stage: "", amount: "", note: "" });
      }
    } catch (e) { setErr(errorText(e)); }
  };
  return (
    <div className="editor add-line">
      <div className="row">
        <label>Service<select value={form.service} onChange={(e) => setForm({ ...form, service: e.target.value })}>
          <option value="">Choose</option>{reference?.service.map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}</select></label>
        <label>Stage<select value={form.stage} onChange={(e) => setForm({ ...form, stage: e.target.value })}>
          <option value="">Choose</option>{reference?.stage.map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}</select></label>
        <label>Fee ({v.currency})<input type="number" min={0} value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></label>
      </div>
      <label>How it was priced (required)<input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })}
        placeholder="e.g. construction review, 12 site visits at AED 15,000" /></label>
      <div className="row"><button className="primary" disabled={!ok} onClick={add}>Add line</button><button onClick={() => setOpen(false)}>Cancel</button></div>
      {err && <p className="error">{err}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- history

const ACTION: Record<string, string> = {
  create: "Created and priced", edit_section: "Edited a section", edit_line: "Changed a fee line", add_line: "Added a fee line",
  delete_line: "Removed a fee line", reprice: "Repriced", draft: "Drafted", approve: "Approved", new_version: "Started a new version",
  export: "Exported",
};

function HistoryTab({ proposal, current }: { proposal: ProposalT; current: number }) {
  return (
    <div className="history">
      <section className="group">
        <h2>Versions</h2>
        <table><thead><tr><th>Version</th><th>Status</th><th>Record</th><th className="num">Fee</th><th>Created</th><th>Approved</th></tr></thead>
          <tbody>{proposal.versions.map((x) => (
            <tr key={x.version_no} className={x.version_no === current ? "current" : undefined}>
              <td><a href={versionHash(proposal.proposal_id, x.version_no)}>Version {x.version_no}</a></td>
              <td><span className={`stamp-sm status-${x.status}`}>{VERSION_STATUS[x.status]}</span></td>
              <td>v{x.record_version}</td>
              <td className="num">{money(x.fee_total_local, x.currency)}</td>
              <td>{x.created_by}<br /><span className="dim">{formatDate(x.created_at)}</span></td>
              <td>{x.approved_by ?? ""}<br /><span className="dim">{x.approved_at ? formatDate(x.approved_at) : ""}</span></td>
            </tr>))}
          </tbody></table>
      </section>
      <section className="group">
        <h2>Every change</h2>
        <p className="lede">Kept permanently. Approved versions cannot be changed, even directly in the database.</p>
        <table><thead><tr><th>When</th><th>Who</th><th>What</th><th>Change</th><th>Reason</th></tr></thead>
          <tbody>{[...proposal.events].reverse().map((e) => (
            <tr key={e.event_id}>
              <td>{new Date(e.at).toLocaleString("en-GB")}</td><td>{e.actor}</td>
              <td>{ACTION[e.action] ?? e.action}{e.target ? <><br /><span className="dim">{codeLabel(e.target)}</span></> : null}</td>
              <td className="change">{summarise(e)}</td><td>{e.reason}</td>
            </tr>))}</tbody></table>
      </section>
    </div>
  );
}

function summarise(e: ProposalEvent): string {
  const pick = (x: unknown, k: string): unknown => (typeof x === "object" && x !== null && k in x ? Object.entries(x).find(([key]) => key === k)?.[1] : undefined);
  const amount = (x: unknown) => { const n = pick(x, "amount_local"); return typeof n === "number" ? Math.round(n).toLocaleString("en-GB") : "?"; };
  switch (e.action) {
    case "edit_line": return `${amount(e.old)} → ${amount(e.new)}`;
    case "draft": return `sections: ${String(pick(e.new, "sections") ?? "")}`;
    case "create": case "reprice": case "approve": {
      const t = pick(e.new, "fee_total_local");
      return typeof t === "number" ? `fee ${Math.round(t).toLocaleString("en-GB")}` : "";
    }
    case "edit_section": return "text changed";
    case "export": return String(pick(e.new, "files") ?? "");
    default: return e.new ? JSON.stringify(e.new).slice(0, 120) : "";
  }
}
