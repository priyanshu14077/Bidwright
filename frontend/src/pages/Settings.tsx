import { useCallback, useEffect, useMemo, useState } from "react";
import { settings, type Clause, type PricingSettings, type Reference } from "../api";
import { InlineEdit } from "../components/Edit";
import { COST_BASIS, COST_TYPE, codeLabel, countryName, errorText } from "../format";
import { useCan } from "../session";
import { ReferenceData } from "./Admin";

export type SettingsTab = "vocabulary" | "pricing" | "library" | "studios";
const TABS: [SettingsTab, string][] = [["vocabulary", "Vocabulary & markets"], ["pricing", "Pricing"],
  ["library", "Standard text"], ["studios", "Studios"]];

export function Settings({ tab, reference, onChanged }: { tab: SettingsTab; reference: Reference | null; onChanged: () => void }) {
  return (
    <div className="page admin settings">
      <h1>Set up</h1>
      <p className="lede">What the engine prices with and the text proposals are built from. Every workspace starts
        from the same starter pack; every change here is logged with who made it and why.</p>
      <nav className="tabs settings-tabs" role="tablist">
        {TABS.map(([k, label]) => (
          <a key={k} role="tab" href={`#/settings/${k}`} aria-selected={tab === k} className={tab === k ? "on" : ""}>{label}</a>
        ))}
      </nav>
      {tab === "vocabulary" && <ReferenceData reference={reference} onChanged={onChanged} />}
      {tab === "pricing" && <Pricing />}
      {tab === "library" && <Library reference={reference} />}
      {tab === "studios" && <Studios />}
    </div>
  );
}

function usePricing() {
  const [data, setData] = useState<PricingSettings | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => settings.pricing().then(setData, (e) => setErr(errorText(e))), []);
  useEffect(() => { load(); }, [load]);
  return { data, err, load };
}

// ---------------------------------------------------------------- pricing

function Pricing() {
  const { data, err, load } = usePricing();
  const can = useCan()("reference");
  const [country, setCountry] = useState("AE");
  const [costType, setCostType] = useState("");
  if (err) return <p className="error">{err}</p>;
  if (!data) return <p className="empty">Loading…</p>;
  const costs = data.construction_costs.filter((c) => c.country === country && (!costType || c.cost_type === costType));
  const basisCount = data.construction_costs.reduce<Record<string, number>>((acc, c) => ({ ...acc, [c.basis]: (acc[c.basis] ?? 0) + 1 }), {});

  return (
    <div className="pricing-settings">
      <section className="group readiness">
        <h2>Ready to price?</h2>
        {data.readiness.length === 0 ? <p className="ok">Every market this workspace works in can be priced.</p> : (
          <ul>{data.readiness.map((r) => (
            <li key={r.message} className={r.severity === "blocks" ? "sev-block" : "sev-warn"}>
              <strong>{r.severity === "blocks" ? "Stops pricing" : "Limits pricing"}:</strong> {r.message}
            </li>))}</ul>
        )}
      </section>

      <section className="group">
        <h2>Construction cost book</h2>
        <p className="lede">Building cost per m² of GFA in USD. The engine uses the city's figure for the building type,
          then the city average × the type's ratio, then the same for the whole country, and escalates it from the
          price year at the market's rate. {Object.entries(basisCount).map(([b, n]) => `${n} ${COST_BASIS[b].toLowerCase()}`).join(", ")}.</p>
        <div className="row filters">
          <label>Country<select value={country} onChange={(e) => setCountry(e.target.value)}>
            {data.markets.map((m) => <option key={m.country} value={m.country}>{m.country_name}</option>)}</select></label>
          <label>Building type<select value={costType} onChange={(e) => setCostType(e.target.value)}>
            <option value="">All</option>{Object.entries(COST_TYPE).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        </div>
        <table>
          <thead><tr><th>City</th><th>Building type</th><th className="num">USD/m²</th><th>Year</th><th>Basis</th><th>Source</th></tr></thead>
          <tbody>{costs.map((c) => (
            <tr key={`${c.city}-${c.cost_type}`}>
              <td>{c.city || <em>Whole country</em>}</td>
              <td>{COST_TYPE[c.cost_type] ?? c.cost_type}</td>
              <td className="num">
                <InlineEdit display={Math.round(c.usd_m2).toLocaleString("en-GB")} initial={String(Math.round(c.usd_m2))}
                  input="number" canEdit={can} onSave={(v, reason) => settings.setCost({ country: c.country, city: c.city,
                    cost_type: c.cost_type, usd_m2: Number(v), reason }).then(load)} />
              </td>
              <td>{c.price_year}</td>
              <td><span className={`basis basis-${c.basis}`}>{COST_BASIS[c.basis]}</span></td>
              <td className="dim">{c.source}{c.note ? <><br />{c.note}</> : null}</td>
            </tr>))}</tbody>
        </table>
      </section>

      <section className="group">
        <h2>Markets</h2>
        <p className="lede">Per country: the cost band (landscape and render rates), the factor on the architecture fee %,
          and how fast construction cost rises each year.</p>
        <table>
          <thead><tr><th>Country</th><th>Band</th><th className="num">Fee factor</th><th className="num">Escalation % a year</th></tr></thead>
          <tbody>{data.markets.map((m) => (
            <tr key={m.country}>
              <td>{m.country_name}<br /><span className="dim">{m.national_currency}</span></td>
              <td><InlineEdit display={m.band} initial={m.band} input="select" options={[["high", "high"], ["low", "low"]]} canEdit={can}
                onSave={(v, reason) => settings.setMarket(m.country, { band: v, reason }).then(load)} /></td>
              <td className="num"><InlineEdit display={<>{Number(m.fee_factor)}<br /><span className="dim">{m.fee_factor_source}</span></>}
                initial={String(m.fee_factor)} input="number" canEdit={can}
                onSave={(v, reason) => settings.setMarket(m.country, { fee_factor: Number(v), reason }).then(load)} /></td>
              <td className="num"><InlineEdit display={<>{Number(m.escalation_pct)}<br /><span className="dim">{m.escalation_source}</span></>}
                initial={String(m.escalation_pct)} input="number" canEdit={can}
                onSave={(v, reason) => settings.setMarket(m.country, { escalation_pct: Number(v), reason }).then(load)} /></td>
            </tr>))}</tbody>
        </table>
      </section>

      <section className="group">
        <h2>Fee method</h2>
        <table>
          <thead><tr><th>Setting</th><th className="num">Value</th><th>Unit</th><th>Why</th></tr></thead>
          <tbody>{data.fee_method.map((a) => (
            <tr key={a.key}>
              <td><code>{a.key}</code></td>
              <td className="num"><InlineEdit display={a.value_text} initial={a.value_text} input={a.value === null ? "text" : "number"}
                canEdit={can} onSave={(v, reason) => settings.setFeeMethod(a.key, a.value === null ? v : Number(v), reason).then(load)} /></td>
              <td className="dim">{a.unit}</td><td className="dim">{a.rationale}</td>
            </tr>))}</tbody>
        </table>
      </section>

      <Changes changes={data.changes} />
    </div>
  );
}

function Changes({ changes }: { changes: PricingSettings["changes"] }) {
  return (
    <section className="group">
      <h2>Recent changes</h2>
      {!changes.length ? <p className="empty">No changes yet.</p> : (
        <table><thead><tr><th>When</th><th>Who</th><th>Setting</th><th>Change</th><th>Reason</th></tr></thead>
          <tbody>{changes.map((c) => (
            <tr key={c.change_id}>
              <td>{new Date(c.changed_at).toLocaleString("en-GB")}</td><td>{c.changed_by}</td>
              <td>{c.setting.replace(/_/g, " ")}<br /><span className="dim">{c.key}</span></td>
              <td className="change">{c.old ? `${JSON.stringify(c.old).slice(0, 80)} → ` : ""}{JSON.stringify(c.new).slice(0, 80)}</td>
              <td>{c.reason}</td>
            </tr>))}</tbody></table>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- standard text

function Library({ reference }: { reference: Reference | null }) {
  const [clauses, setClauses] = useState<Clause[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const can = useCan()("reference");
  const load = useCallback(() => settings.library().then(setClauses, (e) => setErr(errorText(e))), []);
  useEffect(() => { load(); }, [load]);
  const byKind = useMemo(() => {
    const out: Record<Clause["kind"], Clause[]> = { scope: [], exclusion: [], terms: [], position: [] };
    for (const c of clauses ?? []) out[c.kind].push(c);
    return out;
  }, [clauses]);
  if (err) return <p className="error">{err}</p>;
  if (!clauses || !reference) return <p className="empty">Loading…</p>;
  const scope = new Map(byKind.scope.map((c) => [c.key, c]));
  const save = (kind: string, key: string) => (body: string, reason: string) =>
    settings.setClause(kind, key, { body, reason }).then(load);
  const nextExclusion = `e${Math.max(0, ...byKind.exclusion.map((c) => Number(c.key.slice(1)) || 0)) + 1}`;

  return (
    <div className="library">
      <p className="lede">Proposals take this text word for word. Claude only adds what is specific to a request.
        <code>{"{firm}"}</code> and <code>{"{payment_days}"}</code> are filled in when a proposal is drafted.</p>
      <section className="group">
        <h2>Scope of services, by service and stage</h2>
        {reference.service.map((svc) => (
          <div key={svc.code} className="scope-service">
            <h3>{svc.label}</h3>
            <dl>{reference.stage.map((st) => {
              const c = scope.get(`${svc.code}.${st.code}`);
              return (
                <div key={st.code} className="dl-row">
                  <dt>{st.label}</dt>
                  <dd><InlineEdit display={c ? c.body : <span className="dim">No standard text. A proposal with this stage is marked [to be written].</span>}
                    initial={c?.body ?? ""} input="textarea" canEdit={can} onSave={save("scope", `${svc.code}.${st.code}`)} /></dd>
                </div>);
            })}</dl>
          </div>
        ))}
      </section>
      <section className="group">
        <h2>Exclusions</h2>
        <ul className="clause-list">{byKind.exclusion.map((c) => (
          <li key={c.key}>
            <InlineEdit display={c.body} initial={c.body} canEdit={can} onSave={save("exclusion", c.key)} />
            {can && <RemoveClause kind="exclusion" k={c.key} onDone={load} />}
          </li>))}</ul>
        {can && <InlineEdit display={<span className="dim">Add an exclusion</span>} initial="" canEdit
          onSave={save("exclusion", nextExclusion)} />}
      </section>
      <section className="group">
        <h2>Terms</h2>
        <dl>{byKind.terms.map((c) => (
          <div key={c.key} className="dl-row"><dt>{c.key === "standard" ? "Standard terms" : "Sign-off"}</dt>
            <dd><InlineEdit display={c.body} initial={c.body} input="textarea" canEdit={can} onSave={save("terms", c.key)} /></dd></div>))}</dl>
        <h2 className="spaced">Your position when a client asks to change a clause</h2>
        <dl>{byKind.position.map((c) => (
          <div key={c.key} className="dl-row"><dt>{c.label ?? codeLabel(c.key)}</dt>
            <dd><InlineEdit display={c.body} initial={c.body} canEdit={can} onSave={save("position", c.key)} /></dd></div>))}</dl>
      </section>
    </div>
  );
}

function RemoveClause({ kind, k, onDone }: { kind: string; k: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  if (!open) return <button className="edit-link" onClick={() => setOpen(true)}>Remove</button>;
  return (
    <span className="row">
      <input placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <button disabled={reason.trim().length < 3} onClick={() => settings.deleteClause(kind, k, reason).then(onDone)}>Remove</button>
      <button className="quiet" onClick={() => setOpen(false)}>Cancel</button>
    </span>
  );
}

// ---------------------------------------------------------------- studios

function Studios() {
  const { data, err, load } = usePricing();
  const can = useCan()("reference");
  const [form, setForm] = useState({ code: "", name: "", city: "", country: "" });
  const [msg, setMsg] = useState<string | null>(null);
  if (err) return <p className="error">{err}</p>;
  if (!data) return <p className="empty">Loading…</p>;
  const save = async () => {
    try {
      await settings.setStudio(form.code, { name: form.name, city: form.city, country: form.country, reason: "studio added or updated" });
      setMsg(`Saved ${form.code.toUpperCase()}.`); setForm({ code: "", name: "", city: "", country: "" }); load();
    } catch (e) { setMsg(errorText(e)); }
  };
  return (
    <div className="studios">
      <p className="lede">Your offices. When the studio running a project is in another country, the engine adds site
        trips to the reimbursables: regional within the same region, long haul otherwise.</p>
      {!data.studios.length ? <p className="sev-warn">No studios yet, so site trips are never priced.</p> : (
        <table><thead><tr><th>Code</th><th>Name</th><th>City</th><th>Country</th>{can && <th />}</tr></thead>
          <tbody>{data.studios.map((s) => (
            <tr key={s.code}><td><strong>{s.code}</strong></td><td>{s.name}</td><td>{s.city}</td><td>{countryName(s.country)}</td>
              {can && <td><button className="edit-link" onClick={() => setForm({ ...s })}>Edit</button></td>}</tr>))}</tbody></table>
      )}
      {can && (
        <div className="form spaced">
          <h2>{data.studios.some((s) => s.code === form.code.toUpperCase()) ? "Edit studio" : "Add a studio"}</h2>
          <div className="row">
            <label>Code<input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="DXB" /></label>
            <label>Country<select value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })}>
              <option value="">Choose</option>{data.markets.map((m) => <option key={m.country} value={m.country}>{m.country_name}</option>)}</select></label>
          </div>
          <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Dubai studio" /></label>
          <label>City<input value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} /></label>
          <button className="primary" disabled={!form.code || !form.name || !form.city || !form.country} onClick={save}>Save studio</button>
          {msg && <p role="status">{msg}</p>}
        </div>
      )}
      <Changes changes={data.changes.filter((c) => c.setting === "studio")} />
    </div>
  );
}
