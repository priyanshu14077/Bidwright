import { useEffect, useState } from "react";
import { api, type IntakeEvaluation, type Evaluation, type Observability, type Reference } from "../api";
import { codeLabel } from "../format";
import { useCan } from "../session";

export function ReferenceData({ reference, onChanged }: { reference: Reference | null; onChanged: () => void }) {
  const [syn, setSyn] = useState({ synonym: "", target_table: "stage", target_code: "", scope: "global" });
  const [msg, setMsg] = useState<string | null>(null);
  const can = useCan();
  if (!reference) return <p className="empty">Loading…</p>;
  const codes = syn.target_table === "stage" ? reference.stage : syn.target_table === "service" ? reference.service : reference.typology;
  const add = async () => {
    await api.addSynonym(syn);
    setMsg(`"${syn.synonym}" now maps to ${codeLabel(syn.target_code)}.`);
    setSyn({ ...syn, synonym: "" });
    onChanged();
  };
  return (
    <div>
      <p className="lede">The vocabulary, markets and bid rules the engine checks every RFP against. Teach it the terms your clients use.</p>
      <div className="cols">
        <section className="group">
          <h2>Controlled terms</h2>
          {(["typology", "service", "stage"] as const).map((t) => (
            <p key={t}><strong>{t === "stage" ? "Stages" : t === "service" ? "Services" : "Typologies"}:</strong> {reference[t].map((r) => r.label).join(", ")}</p>
          ))}
          {can("reference") && <><h2>Teach a new term</h2>
          <div className="form">
            <label>Term as written<input value={syn.synonym} onChange={(e) => setSyn({ ...syn, synonym: e.target.value })} placeholder="e.g. Contract Documentation" /></label>
            <label>List<select value={syn.target_table} onChange={(e) => setSyn({ ...syn, target_table: e.target.value, target_code: "" })}>
              <option value="stage">Stage</option><option value="service">Service</option><option value="typology">Typology</option></select></label>
            <label>Means<select value={syn.target_code} onChange={(e) => setSyn({ ...syn, target_code: e.target.value })}>
              <option value="">Choose</option>{codes.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}</select></label>
            <label>Only in country<input value={syn.scope} onChange={(e) => setSyn({ ...syn, scope: e.target.value || "global" })} placeholder="global or ISO code, e.g. AU" /></label>
            <button className="primary" disabled={!syn.synonym || !syn.target_code} onClick={add}>Add term</button>
            {msg && <p role="status">{msg}</p>}
          </div></>}
        </section>
        <section className="group">
          <h2>Synonyms ({reference.synonyms.length})</h2>
          <table><thead><tr><th>As written</th><th>Means</th><th>Where</th></tr></thead>
            <tbody>{reference.synonyms.map((s) => (
              <tr key={`${s.synonym}-${s.target_table}-${s.scope}`}><td>{s.synonym}</td><td>{codeLabel(s.target_code)}</td><td>{s.scope}</td></tr>))}
            </tbody></table>
        </section>
      </div>
      <div className="cols">
        <section className="group">
          <h2>Markets and location tiers</h2>
          <table><thead><tr><th>City</th><th>Country</th><th>Tier</th><th>Currency</th></tr></thead>
            <tbody>{reference.locations.map((l) => (
              <tr key={`${l.country}-${l.city}`}><td>{l.city}</td><td>{l.country_name}</td><td>{l.tier}</td><td>{l.national_currency}</td></tr>))}
            </tbody></table>
        </section>
        <section className="group">
          <h2>Qualification rules</h2>
          <ul>{reference.rules.map((r) => <li key={r.name}><strong>{r.effect.replace(/_/g, " ")}</strong>: {r.message}</li>)}</ul>
          <h2>Archive ({reference.archive.length} proposals)</h2>
          {!reference.archive.length && <p className="dim">No past proposals yet. Comparables and fee benchmarks appear once your archive is loaded.</p>}
          {reference.archive.length > 0 && <table><thead><tr><th>Ref</th><th>Project</th><th>Outcome</th><th>Data</th></tr></thead>
            <tbody>{reference.archive.map((p) => (
              <tr key={p.proposal_id}><td>{p.reference}</td><td>{p.title}<br /><span className="dim">{p.city}, {codeLabel(p.typology)}</span></td>
                <td>{p.status}</td><td>{p.data_origin}, {p.dataset_version}</td></tr>))}
            </tbody></table>}
        </section>
      </div>
    </div>
  );
}

export function ModelLog() {
  const [o, setO] = useState<Observability | null>(null);
  useEffect(() => { api.observability().then(setO); }, []);
  if (!o) return <div className="page"><p className="empty">Loading…</p></div>;
  const t = o.totals;
  return (
    <div className="page admin">
      <h1>Model calls</h1>
      <p className="lede">Every request to the model is logged with its prompt version, tokens, cost and latency. RFP text is treated as data; the model can only return extracted values.</p>
      <dl className="figures">
        <div><dt>Calls</dt><dd>{t.calls}</dd></div>
        <div><dt>Cost</dt><dd>${Number(t.cost_usd ?? 0).toFixed(2)}</dd></div>
        <div><dt>Median latency</dt><dd>{t.p50_ms ? `${(t.p50_ms / 1000).toFixed(1)} s` : ""}</dd></div>
        <div><dt>95th percentile</dt><dd>{t.p95_ms ? `${(t.p95_ms / 1000).toFixed(1)} s` : ""}</dd></div>
        <div><dt>Rejected answers</dt><dd>{t.rejected}</dd></div>
        <div><dt>Errors</dt><dd>{t.errors}</dd></div>
      </dl>
      <section className="group">
        <h2>By prompt version</h2>
        <table><thead><tr><th>Model</th><th>Prompt</th><th>Calls</th><th>Cost</th><th>Avg latency</th><th>Rejected</th></tr></thead>
          <tbody>{o.by_version.map((v) => (
            <tr key={v.model + v.prompt_version}><td>{v.model}</td><td>{v.prompt_version}</td><td className="num">{v.calls}</td>
              <td className="num">${Number(v.cost_usd ?? 0).toFixed(2)}</td><td className="num">{(v.avg_ms / 1000).toFixed(1)} s</td><td className="num">{v.rejected}</td></tr>))}
          </tbody></table>
      </section>
      <section className="group">
        <h2>Recent calls</h2>
        <table><thead><tr><th>Call</th><th>Document</th><th>Tokens in / out</th><th>Cached</th><th>Latency</th><th>Cost</th><th>Result</th></tr></thead>
          <tbody>{o.recent.map((c) => (
            <tr key={c.call_id}><td>{c.call_id}<br /><span className="dim">attempt {c.attempt}</span></td>
              <td>{c.file_name}<br /><span className="dim">envelope {c.envelope_id}</span></td>
              <td className="num">{c.input_tokens?.toLocaleString()} / {c.output_tokens?.toLocaleString()}</td>
              <td className="num">{c.cache_read_tokens?.toLocaleString()}</td>
              <td className="num">{c.latency_ms ? `${(c.latency_ms / 1000).toFixed(1)} s` : ""}</td>
              <td className="num">{c.cost_usd != null ? `$${Number(c.cost_usd).toFixed(3)}` : ""}</td>
              <td>{c.error ?? c.validation_error ?? c.stop_reason}</td></tr>))}
          </tbody></table>
      </section>
    </div>
  );
}

const FIELD_LABEL: Record<string, string> = {
  typology: "Typology", gfa_m2: "GFA / BUA", site_area_m2: "Site area", fitout_area_m2: "Interior area",
  landscape_area_m2: "Landscape area", services: "Services", stage_package: "Stages", country: "Country", city: "City",
  client_name: "Client", client_status: "New or repeat client", units: "Units", keys: "Keys",
  submission_deadline: "Deadline", liability_cap: "Liability", payment_terms_days: "Payment terms",
};

export function Accuracy({ onOpen }: { onOpen: (id: number) => void }) {
  return (
    <div className="page admin">
      <h1>Backtests</h1>
      <p className="lede">How Bidwright scores against answers known in advance: first on incoming requests (WhatsApp messages, emails, call notes, RFP packs), then on the practice's past RFP packs.</p>
      <IntakeBacktest />
      <PackBacktest onOpen={onOpen} />
    </div>
  );
}

const KIND_LABEL: Record<string, string> = { go: "Qualify", clarify: "Clarify", "no-go": "Decline" };

/** The intake run on the sample requests (make eval-intake): recommendation, field status and questions. */
function IntakeBacktest() {
  const [e, setE] = useState<IntakeEvaluation | null | undefined>(undefined);
  useEffect(() => { api.intakeEvaluation().then(setE, () => setE(null)); }, []);
  if (e === undefined) return <p className="empty">Loading…</p>;
  if (e === null) return (
    <section>
      <h2>Incoming requests</h2>
      <p className="empty">No run on the sample requests yet. An admin starts one with <code>make eval-intake</code>.</p>
    </section>);
  const s = e.summary, pct = (v: number) => `${Math.round(v * 100)}%`;
  const kinds = ["go", "clarify", "no-go"];
  return (
    <section>
      <h2>Incoming requests</h2>
      <p className="lede">The latest run over {s.requests} sample requests, each with the answer known in advance: whether to qualify,
        clarify or decline; which values are found, estimated or missing; and whether the questions for the client cover what is missing.</p>
      <dl className="figures">
        <div><dt>Recommendation right</dt><dd>{pct(s.recommendation_accuracy)}</dd><dd className="dim">qualify, clarify or decline</dd></div>
        <div><dt>Field status right</dt><dd>{pct(s.status_accuracy)}</dd><dd className="dim">values {pct(s.value_accuracy)} right</dd></div>
        <div><dt>Questions cover the gaps</dt><dd>{pct(s.question_coverage)}</dd><dd className="dim">gaps caught {s.gaps_caught}</dd></div>
        <div><dt>Model cost, whole set</dt><dd>${s.cost_usd.toFixed(2)}</dd><dd className="dim">{s.llm_calls} calls · false gaps {s.false_gaps}</dd></div>
      </dl>
      <p className="dim">Run {e.meta.run_at}, {e.meta.model}, prompt {e.meta.prompt_version}, engine {e.meta.engine_version}, dataset {e.meta.dataset}.</p>
      <div className="grid-wrap">
        <table className="grid grid--matrix">
          <caption>Expected recommendation (rows) against what Bidwright said (columns)</caption>
          <thead><tr><th>Expected</th>{kinds.map((k) => <th key={k}>Said {KIND_LABEL[k]}</th>)}</tr></thead>
          <tbody>{kinds.map((k) => (
            <tr key={k}><td><strong>{KIND_LABEL[k]}</strong></td>
              {kinds.map((g) => {
                const n = s.recommendation_confusion[k]?.[g] ?? 0;
                return <td key={g} className={n ? (k === g ? "ok" : "miss") : ""}>{n || ""}</td>;
              })}
            </tr>))}
          </tbody>
        </table>
      </div>
      <div className="grid-wrap">
        <table className="grid">
          <thead><tr><th>Request</th><th>Kind</th><th>Recommendation</th><th>Fields right</th><th>Missed</th></tr></thead>
          <tbody>{e.results.map((r) => (
            <tr key={r.case_id}>
              <td><strong>{r.case_id}</strong></td>
              <td className="dim">{(r.kind ?? "").replace(/_/g, " ")}</td>
              <td className={r.recommendation?.ok ? "ok" : "miss"}>
                {r.recommendation?.ok ? "✓ " : "✗ "}{KIND_LABEL[r.recommendation?.got] ?? r.recommendation?.got}
                {!r.recommendation?.ok && <span className="dim"> (expected {KIND_LABEL[r.recommendation?.expected] ?? r.recommendation?.expected})</span>}
              </td>
              <td>{r.fields_ok}/{r.fields}</td>
              <td className="dim">{r.missed.map((f) => FIELD_LABEL[f] ?? f.replace(/_/g, " ")).join(", ")}</td>
            </tr>))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PackBacktest({ onOpen }: { onOpen: (id: number) => void }) {
  const [e, setE] = useState<Evaluation | null | undefined>(undefined);
  useEffect(() => { api.evaluation().then(setE); }, []);
  if (e === undefined) return <p className="empty">Loading…</p>;
  if (e === null) return (
    <section>
      <h2>RFP packs</h2>
      <p className="lede">Runs the extraction on every pack in your gold set, RFP packs paired with the proposals your practice actually wrote, and scores each value against the answer.</p>
      <p className="empty">No run on the RFP packs yet. An admin starts one with <code>make eval</code> (the whole gold set) or <code>make eval-one CASE=P13</code> (one pack).</p>
    </section>);
  const s = e.summary;
  const fieldsList = Object.keys(s.per_field);
  return (
    <section>
      <h2>RFP packs</h2>
      <p className="lede">The latest run over {e.results.length} gold packs, each paired with the proposal the practice wrote. What Bidwright extracts is compared with what the proposal says. Click a pack to open its record.</p>
      <dl className="figures">
        <div><dt>Pricing-critical fields</dt><dd>{(s.pricing_critical_accuracy * 100).toFixed(1)}%</dd><dd className="dim">target 90%</dd></div>
        <div><dt>Values with a source highlight</dt><dd>{(s.citation_coverage * 100).toFixed(1)}%</dd><dd className="dim">target 100%</dd></div>
        <div><dt>Planted conflicts flagged</dt><dd>{s.conflict_recall}</dd><dd className="dim">{s.false_conflicts} not planted</dd></div>
        <div><dt>Model cost, whole set</dt><dd>${s.cost_usd.toFixed(2)}</dd><dd className="dim">{s.llm_calls} calls</dd></div>
      </dl>
      <p className="dim">Run {e.meta.run_at}, {e.meta.model}, prompt {e.meta.prompt_version}, engine {e.meta.engine_version}, dataset {e.meta.dataset}.</p>
      <div className="grid-wrap">
        <table className="grid">
          <thead><tr><th>Pack</th>{fieldsList.map((f) => <th key={f}>{FIELD_LABEL[f] ?? f}</th>)}<th>Conflict</th></tr></thead>
          <tbody>{e.results.map((r) => (
            <tr key={r.proposal_id} onClick={() => onOpen(r.envelope_id)} tabIndex={0}>
              <td><strong>{r.proposal_id}</strong><br /><span className="dim">{r.traps.join(", ").replace(/_/g, " ")}</span></td>
              {fieldsList.map((f) => {
                const c = r.fields[f];
                return <td key={f} className={c.ok ? "ok" : "miss"} title={c.ok ? "" : `Expected ${JSON.stringify(c.truth)}, got ${JSON.stringify(c.got)}`}>{c.ok ? "✓" : "✗"}</td>;
              })}
              <td>{r.conflicts.planted.length ? (r.conflicts.detected.length ? "△ found" : "missed") : r.conflicts.false.length ? "extra" : ""}</td>
            </tr>))}
            <tr className="totals"><td>Per field</td>{fieldsList.map((f) => <td key={f}>{Math.round(s.per_field[f] * 100)}%</td>)}<td /></tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}
