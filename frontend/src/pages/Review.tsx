import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Audit, type Detail, type Mapping, type RecordField, type Reference, type Source } from "../api";
import { PdfViewer, type Highlight } from "../components/PdfViewer";
import {
  DOC_CLASS, LEAD, ORIGIN, codeLabel, countryName, daysLeft, formatDate, formatDateTime, formatValue, money,
} from "../format";
import { useCan } from "../session";

type Tab = "record" | "sections" | "references" | "audit";
const GROUP_ORDER = ["Client", "Location", "Project", "Areas", "Scope", "Dates", "Commercial", "Enrichment"];

export function Review({ id, reference, onBack }: { id: number; reference: Reference | null; onBack: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [docId, setDocId] = useState<number | null>(null);
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("record");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => api.detail(id).then((d) => {
    setDetail(d);
    setDocId((cur) => cur ?? d.documents.find((x) => x.doc_class === "main_rfp")?.document_id
      ?? d.documents.find((x) => x.has_pdf)?.document_id ?? null);
  }).catch((e: Error) => setError(e.message)), [id]);

  useEffect(() => { load(); }, [load]);
  const busy = detail && ["reading", "extracting"].includes(detail.envelope.status);
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [busy, load]);

  const show = (field: string, s: Source | undefined, tone: Highlight["tone"] = "source") => {
    setActive(field);
    if (!s || !s.document_id || !s.page || !s.bbox?.length) { setHighlight(null); return; }
    setDocId(s.document_id);
    setHighlight({ documentId: s.document_id, page: s.page, rects: s.bbox, tone });
  };

  if (error) return <div className="page"><p className="error">{error}</p></div>;
  if (!detail) return <div className="page"><p className="empty">Loading…</p></div>;
  const { envelope: env, record, documents } = detail;
  const doc = documents.find((d) => d.document_id === docId);
  const openConflicts = Object.values(record).filter((f): f is RecordField => "conflict" in f && !!f.conflict).length;

  return (
    <div className="review">
      <TitleBlock detail={detail} openConflicts={openConflicts} onBack={onBack} onChanged={load} />
      <div className="split">
        <section className="sheets" aria-label="Source documents">
          <ol className="register">
            {documents.map((d) => (
              <li key={d.document_id}>
                <button className={d.document_id === docId ? "on" : ""} disabled={!d.has_pdf}
                        onClick={() => setDocId(d.document_id)}>
                  <span className="register-name">{d.file_name}</span>
                  <span className="register-meta">
                    {d.has_pdf ? `${DOC_CLASS[d.doc_class ?? ""] ?? "Unclassified"}, ${d.page_count} p.` : "Stored, not read in Phase 1"}
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className="sheet-scroll">
            {doc?.has_pdf
              ? <PdfViewer url={api.pdfUrl(doc.document_id)} documentId={doc.document_id} highlight={highlight} width={600} />
              : <p className="empty">Select a document.</p>}
          </div>
        </section>
        <section className="panel">
          <nav className="tabs" role="tablist">
            {([["record", "Record"], ["sections", "Proposal sections"], ["references", "Reference projects"],
               ["audit", "Audit trail"]] as const).map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                {label}
              </button>
            ))}
          </nav>
          <div className="panel-scroll">
            {busy && <p className="working">Reading the pack and extracting values. This takes about a minute.</p>}
            {env.status === "failed" && <p className="error">Extraction failed: {env.error}</p>}
            {tab === "record" && !busy && (
              <RecordView detail={detail} reference={reference} active={active} onShow={show} onChanged={load} />
            )}
            {tab === "sections" && <Sections id={id} version={env.status + openConflicts} />}
            {tab === "references" && <References detail={detail} />}
            {tab === "audit" && <AuditTrail id={id} stamp={JSON.stringify(record).length} />}
          </div>
        </section>
      </div>
    </div>
  );
}

function TitleBlock({ detail, openConflicts, onBack, onChanged }:
  { detail: Detail; openConflicts: number; onBack: () => void; onChanged: () => void }) {
  const { envelope: env, record } = detail;
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("Checked every value against its source");
  const [msg, setMsg] = useState<string | null>(null);
  const left = daysLeft(env.submission_deadline, env.received_at);
  const project = String(record.project_name?.value ?? env.title ?? `Envelope ${env.envelope_id}`);
  const confirmed = env.status === "confirmed";
  const can = useCan();

  const confirm = async () => {
    try {
      const r = await api.confirm(env.envelope_id, reason);
      setMsg(`Confirmed as version ${r.version}`);
      setConfirming(false);
      onChanged();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <header className="titleblock">
      <div className="tb-cell tb-project">
        <button className="link" onClick={onBack}>Inbox</button>
        <h1>{project}</h1>
        <p>{String(record.client_name?.value ?? "")}{record.client_status?.value ? `, ${record.client_status.value} client` : ""}</p>
      </div>
      <div className="tb-cell">
        <span className="tb-label">Location</span>
        <span>{[record.city?.value, countryName(env.country)].filter(Boolean).join(", ")}</span>
        <span className="tb-sub">{env.tier ? `${env.tier} tier` : "tier unknown"}{env.billing_currency ? `, bills in ${env.billing_currency}` : ""}</span>
      </div>
      <div className="tb-cell">
        <span className="tb-label">Deadline</span>
        <span>{env.submission_deadline ? formatDateTime(String(record.submission_deadline?.value ?? env.submission_deadline)) : "Not stated"}</span>
        <span className="tb-sub">{left != null ? `${left} days from receipt` : ""}</span>
      </div>
      <div className="tb-cell">
        <span className="tb-label">Lead status</span>
        <span className={`lead lead-${env.lead_status}`}>{env.lead_status ? LEAD[env.lead_status] : "Pending"}</span>
        <span className="tb-sub">{env.completeness != null ? `${Math.round(env.completeness * 100)}% of pricing fields` : ""}</span>
      </div>
      <div className="tb-cell tb-actions">
        {confirmed ? (
          <span className="stamp">Confirmed</span>
        ) : !can("review") ? (
          <span className="tb-sub">Your role can read this record but not change it.</span>
        ) : confirming ? (
          <div className="confirm-box">
            <label>Reason for confirming
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
            <div className="row">
              <button className="primary" onClick={confirm} disabled={reason.trim().length < 3}>Confirm record</button>
              <button onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          </div>
        ) : (
          <>
            <button className="primary" disabled={openConflicts > 0 || env.status !== "review"} onClick={() => setConfirming(true)}>
              Confirm record
            </button>
            <span className="tb-sub">{openConflicts > 0 ? `Resolve ${openConflicts} conflict${openConflicts > 1 ? "s" : ""} first` : "Locks this version"}</span>
            <button className="quiet" onClick={() => api.rerun(env.envelope_id).then(onChanged)} disabled={env.status === "extracting"}>
              {env.status === "received" ? "Extract" : "Extract again"}
            </button>
          </>
        )}
        {msg && <span className="tb-sub" role="status">{msg}</span>}
      </div>
    </header>
  );
}

function RecordView({ detail, reference, active, onShow, onChanged }: {
  detail: Detail; reference: Reference | null; active: string | null;
  onShow: (field: string, s: Source | undefined, tone?: Highlight["tone"]) => void; onChanged: () => void;
}) {
  const { record, envelope } = detail;
  const groups = useMemo(() => {
    const out: Record<string, [string, RecordField][]> = {};
    for (const [name, f] of Object.entries(record)) {
      if (name.startsWith("_") || !("group" in f)) continue;
      (out[f.group] ??= []).push([name, f]);
    }
    return GROUP_ORDER.filter((g) => out[g]).map((g) => [g, out[g]] as const);
  }, [record]);
  const leadFlags = record._flags.filter((f) => f.rule.startsWith("rule:") || f.rule === "missing_critical");

  return (
    <div className="record">
      {leadFlags.length > 0 && (
        <div className="why">
          <h2>Why {envelope.lead_status ? LEAD[envelope.lead_status].toLowerCase() : "this status"}</h2>
          <ul>{leadFlags.map((f, i) => <li key={i} className={`sev-${f.severity}`}>{f.message}</li>)}</ul>
        </div>
      )}
      {groups.map(([group, items]) => (
        <section key={group} className="group">
          <h2>{group === "Enrichment" ? "Added by the reference engine" : group}</h2>
          {items.map(([name, f]) => (
            <FieldRow key={name} id={envelope.envelope_id} name={name} f={f} reference={reference}
                      locked={envelope.status === "confirmed"} active={active === name} onShow={onShow} onChanged={onChanged} />
          ))}
        </section>
      ))}
    </div>
  );
}

function FieldRow({ id, name, f, reference, locked, active, onShow, onChanged }: {
  id: number; name: string; f: RecordField; reference: Reference | null; locked: boolean; active: boolean;
  onShow: (field: string, s: Source | undefined, tone?: Highlight["tone"]) => void; onChanged: () => void;
}) {
  const [editing, setEditing] = useState<null | { action: "edit" | "resolve_conflict" | "accept_suggestion"; fvId?: number }>(null);
  const [draft, setDraft] = useState<unknown>(f.value);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const canEdit = useCan()("review");
  const live = f.sources.filter((s) => s.status === "proposed" || s.status === "confirmed");
  const primary = live.find((s) => s.origin === "human") ?? live.find((s) => s.origin === "ai") ?? live[0];
  const corroborating = f.sources.filter((s) => s.origin === "ai" && s.status === "superseded");
  const empty = f.value == null || (Array.isArray(f.value) && !f.value.length);
  const fieldFlags = f.flags.filter((fl) => fl.rule !== "conflict");

  const save = async () => {
    if (!editing) return;
    setErr(null);
    try {
      await api.change(id, name, { action: editing.action, reason, value: draft, field_value_id: editing.fvId });
      setEditing(null);
      setReason("");
      onChanged();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div className={`field${active ? " active" : ""}${f.conflict ? " has-conflict" : ""}`}>
      <button className="field-main" onClick={() => onShow(name, primary)} aria-label={`Show source for ${f.label}`}>
        <span className="field-label">{f.label}</span>
        <span className={`field-value${empty ? " missing" : ""}`}>
          {empty ? "Not stated" : formatValue(f.kind, f.value)}
          {f.conflict && <span className="delta" title="Conflict between documents">△</span>}
        </span>
        <span className="field-source">
          {primary?.file_name ? `${primary.file_name}, p. ${primary.page}` : primary ? ORIGIN[primary.origin] : ""}
          {f.edited && <em> · changed by reviewer</em>}
          {corroborating.length > 0 && !f.is_list && <em> · also in {corroborating.length} other place{corroborating.length > 1 ? "s" : ""}</em>}
        </span>
      </button>

      {f.conflict && !locked && (
        <div className="conflict">
          <p>The documents disagree. Choose the value to keep:</p>
          {f.conflict.field_value_ids.map((fvId) => {
            const s = f.sources.find((x) => x.field_value_id === fvId);
            if (!s) return null;
            return (
              <div key={fvId} className="choice">
                <button className="link" onClick={() => onShow(name, s, "conflict")}>
                  {s.value_text} <span className="dim">({DOC_CLASS[s.doc_class ?? ""] ?? s.file_name}, p. {s.page})</span>
                </button>
                {canEdit && <button onClick={() => { setEditing({ action: "resolve_conflict", fvId }); setReason(""); }}>Keep this</button>}
              </div>
            );
          })}
        </div>
      )}

      {f.suggestion != null && !locked && (
        <div className="suggestion">
          <span>Suggested {formatValue(f.kind, f.suggestion)}. {String(f.suggestion_evidence?.basis ?? "")}</span>
          {canEdit && <button onClick={() => { setEditing({ action: "accept_suggestion" }); setReason(""); }}>Accept suggestion</button>}
        </div>
      )}

      {fieldFlags.length > 0 && (
        <ul className="flags">{fieldFlags.map((fl, i) => <li key={i} className={`sev-${fl.severity}`}>{fl.message}</li>)}</ul>
      )}

      {active && primary?.notes?.some(Boolean) && <p className="note">{primary.notes.filter(Boolean).join("; ")}</p>}
      {active && primary?.origin === "engine" && primary.evidence && (
        <p className="note">{String(primary.evidence.basis ?? JSON.stringify(primary.evidence))}</p>
      )}

      {!locked && canEdit && !editing && (
        <button className="edit" onClick={() => { setDraft(f.value); setEditing({ action: "edit" }); }}>Change</button>
      )}

      {editing && (
        <div className="editor">
          {editing.action === "edit" && <ValueInput f={f} value={draft} onChange={setDraft} reference={reference} />}
          <label>Reason (required)
            <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)}
                   placeholder={editing.action === "resolve_conflict" ? "e.g. the area schedule is the controlling document" : "e.g. confirmed with the client by email"} />
          </label>
          <div className="row">
            <button className="primary" disabled={reason.trim().length < 3} onClick={save}>
              {editing.action === "resolve_conflict" ? "Keep this value" : editing.action === "accept_suggestion" ? "Accept suggestion" : "Save change"}
            </button>
            <button onClick={() => setEditing(null)}>Cancel</button>
          </div>
          {err && <p className="error">{err}</p>}
        </div>
      )}
    </div>
  );
}

function ValueInput({ f, value, onChange, reference }:
  { f: RecordField; value: unknown; onChange: (v: unknown) => void; reference: Reference | null }) {
  const vocab = f.kind === "vocab" || f.kind === "vocab_list"
    ? (f.label === "Typology" ? reference?.typology : f.label === "Services" ? reference?.service : reference?.stage) ?? []
    : [];
  if (f.kind === "vocab_list") {
    const set = new Set(Array.isArray(value) ? value.map(String) : []);
    return (
      <fieldset className="checks"><legend>{f.label}</legend>
        {vocab.map((v) => (
          <label key={v.code}><input type="checkbox" checked={set.has(v.code)} onChange={(e) => {
            const next = new Set(set);
            if (e.target.checked) next.add(v.code); else next.delete(v.code);
            onChange(vocab.map((x) => x.code).filter((c) => next.has(c)));
          }} /> {v.label}</label>
        ))}
      </fieldset>
    );
  }
  if (f.kind === "vocab" || f.kind === "enum") {
    const options = f.kind === "vocab" ? vocab.map((v) => [v.code, v.label]) : [["capped", "Capped"], ["uncapped", "Uncapped"]];
    return (
      <label>{f.label}
        <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          <option value="">Not stated</option>
          {options.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
        </select>
      </label>
    );
  }
  if (f.kind === "area" || f.kind === "integer") {
    return (
      <label>{f.label}{f.kind === "area" ? " (m²)" : ""}
        <input type="number" value={value == null ? "" : String(value)}
               onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
      </label>
    );
  }
  if (f.is_list) {
    return (
      <label>{f.label} (one per line)
        <textarea rows={3} value={Array.isArray(value) ? value.join("\n") : ""}
                  onChange={(e) => onChange(e.target.value.split("\n").map((s) => s.trim()).filter(Boolean))} />
      </label>
    );
  }
  return (
    <label>{f.label}{f.kind === "datetime" ? " (ISO 8601, e.g. 2025-02-03T14:00:00+04:00)" : ""}
      <input value={String(value ?? "")} onChange={(e) => onChange(e.target.value || null)} />
    </label>
  );
}

function Sections({ id, version }: { id: number; version: string }) {
  const [m, setM] = useState<Mapping | null>(null);
  useEffect(() => { api.mapping(id).then(setM); }, [id, version]);
  if (!m) return <p className="empty">Loading…</p>;
  return (
    <div className="sections">
      <p className="lede">The RFP's requirements arranged under the sections of one of your proposals.</p>
      {m.sections.map((s) => (
        <section key={s.code} className="group">
          <h2>{s.title} <span className="dim">in {s.in_archive}</span></h2>
          <dl>
            {s.items.map((it) => (
              <div key={it.field} className="dl-row">
                <dt>{it.label}</dt>
                <dd>{Array.isArray(it.value) ? (it.value.length ? it.value.map((v) => codeLabel(String(v))).join(", ") : "Not stated")
                  : it.value == null ? "Not stated" : typeof it.value === "number" ? it.value.toLocaleString("en-GB") : codeLabel(String(it.value))}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
      <section className="group unmapped">
        <h2>Outside your usual proposal structure</h2>
        <p className="lede">Requests with no place in your standard proposal. These usually change scope or price.</p>
        {m.unmapped.length ? (
          <ul>{m.unmapped.map((u, i) => (
            <li key={i}>{u.text} <span className="dim">{u.kind === "special_request" ? "special request" : u.kind}{u.file_name ? `, ${u.file_name} p. ${u.page}` : ""}</span></li>
          ))}</ul>
        ) : <p className="empty">None found.</p>}
      </section>
    </div>
  );
}

function References({ detail }: { detail: Detail }) {
  const c = detail.comparables;
  if (!c) return <p className="empty">Available once extraction finishes.</p>;
  return (
    <div className="references">
      <p className="lede">{c.label} Fees converted to {c.currency} at rates dated {formatDate(c.fx_as_of)}. {c.fx_note}.</p>
      <table>
        <thead><tr><th>Project</th><th>Scope</th><th>Size</th><th>Fee ({c.currency})</th><th>Per m²</th><th>Outcome</th></tr></thead>
        <tbody>
          {c.items.map((p) => (
            <tr key={p.reference}>
              <td><strong>{p.title}</strong><br /><span className="dim">{p.reference}, {p.city}, {p.year}</span></td>
              <td>{codeLabel(p.typology)}<br /><span className="dim">{p.services.map(codeLabel).join(", ")}</span></td>
              <td className="num">{Math.round(p.size_m2).toLocaleString("en-GB")} m²<br /><span className="dim">{p.size_basis === "gfa_m2" ? "GFA" : "site"}</span></td>
              <td className="num">{money(p.fee_in_target, "")}<br /><span className="dim">{money(p.fee_local, p.currency)} as quoted</span></td>
              <td className="num">{p.fee_per_m2_in_target?.toLocaleString("en-GB", { maximumFractionDigits: 0 })}</td>
              <td>{p.status}{p.loss_reason ? <><br /><span className="dim">{p.loss_reason}</span></> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AuditTrail({ id, stamp }: { id: number; stamp: number }) {
  const [a, setA] = useState<Audit | null>(null);
  const [field, setField] = useState<string>("");
  useEffect(() => { api.audit(id).then(setA); }, [id, stamp]);
  if (!a) return <p className="empty">Loading…</p>;
  const fieldsList = [...new Set(a.values.map((v) => v.field))];
  const values = field ? a.values.filter((v) => v.field === field) : [];
  return (
    <div className="audit">
      <section className="group">
        <h2>Changes by people</h2>
        {a.changes.length ? (
          <table><thead><tr><th>When</th><th>Who</th><th>Field</th><th>Change</th><th>Reason</th></tr></thead>
            <tbody>{a.changes.map((c) => (
              <tr key={c.change_id}>
                <td>{new Date(c.changed_at).toLocaleString("en-GB")}</td><td>{c.changed_by}</td>
                <td>{c.field === "*" ? "Whole record" : c.field}</td>
                <td>{c.action === "confirm" ? "Confirmed" : `${JSON.stringify(c.old_value)} → ${JSON.stringify(c.new_value)}`}</td>
                <td>{c.reason}</td>
              </tr>))}
            </tbody></table>
        ) : <p className="empty">No changes yet. Every change needs a reason and is kept permanently.</p>}
      </section>
      <section className="group">
        <h2>History of a value</h2>
        <select value={field} onChange={(e) => setField(e.target.value)} aria-label="Field">
          <option value="">Choose a field</option>
          {fieldsList.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        {values.length > 0 && (
          <table><thead><tr><th>When</th><th>By</th><th>As written</th><th>Value</th><th>Status</th><th>Source</th></tr></thead>
            <tbody>{values.map((v) => (
              <tr key={v.field_value_id}>
                <td>{new Date(v.created_at).toLocaleString("en-GB")}</td>
                <td>{ORIGIN[v.origin]}{v.model ? <><br /><span className="dim">{v.model}, {v.prompt_version}</span></> : v.engine_version ? <><br /><span className="dim">{v.engine_version}</span></> : null}</td>
                <td>{v.value_text}</td><td>{JSON.stringify(v.normalized)}</td><td>{v.status}{v.kind === "suggestion" ? " (suggestion)" : ""}</td>
                <td>{v.file_name ? `${v.file_name}, p. ${v.page}` : ""}</td>
              </tr>))}
            </tbody></table>
        )}
      </section>
      <section className="group">
        <h2>Machine runs</h2>
        <table><thead><tr><th>Run</th><th>Step</th><th>Document</th><th>Version</th><th>Status</th><th>Cost</th></tr></thead>
          <tbody>{a.runs.map((r) => (
            <tr key={r.run_id}>
              <td>{r.run_id}</td><td>{r.kind}</td><td>{r.file_name ?? ""}</td>
              <td>{r.model ? `${r.model}, ${r.prompt_version}` : r.engine_version}</td><td>{r.status}</td>
              <td className="num">{r.cost_usd != null ? `$${Number(r.cost_usd).toFixed(3)}` : ""}</td>
            </tr>))}
          </tbody></table>
      </section>
      {a.versions.length > 0 && (
        <section className="group">
          <h2>Confirmed versions</h2>
          <ul>{a.versions.map((v) => <li key={v.version}>Version {v.version}, {v.confirmed_by}, {new Date(v.confirmed_at).toLocaleString("en-GB")}</li>)}</ul>
        </section>
      )}
    </div>
  );
}
