import { useState, type ReactNode } from "react";
import type { PricingChoices } from "../api";
import { COST_TYPE, errorText } from "../format";

/** A value with a Change button. Saving asks for a reason, because every change is logged. */
export function InlineEdit({ display, initial, input = "text", options, canEdit, onSave, reasonRequired = true }: {
  display: ReactNode; initial: string; input?: "text" | "number" | "textarea" | "select";
  options?: [string, string][]; canEdit: boolean; reasonRequired?: boolean;
  onSave: (value: string, reason: string) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(initial);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  if (!editing) {
    return (
      <span className="inline-edit">
        {display}
        {canEdit && <button className="edit-link" onClick={() => { setValue(initial); setReason(""); setErr(null); setEditing(true); }}>
          Change</button>}
      </span>
    );
  }
  const save = async () => {
    setSaving(true);
    try { await onSave(value, reason.trim()); setEditing(false); } catch (e) { setErr(errorText(e)); } finally { setSaving(false); }
  };
  return (
    <div className="editor">
      {input === "textarea" ? <textarea rows={Math.min(14, Math.max(4, value.split("\n").length + 1))} value={value}
                                        onChange={(e) => setValue(e.target.value)} autoFocus />
        : input === "select" ? <select value={value} onChange={(e) => setValue(e.target.value)} autoFocus>
            {(options ?? []).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        : <input type={input} value={value} onChange={(e) => setValue(e.target.value)} autoFocus
                 step={input === "number" ? "any" : undefined} />}
      <label>Reason{reasonRequired ? " (required)" : ""}<input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <div className="row">
        <button className="primary" disabled={saving || !value.trim() || (reasonRequired && reason.trim().length < 3)} onClick={save}>
          {saving ? "Saving…" : "Save"}</button>
        <button onClick={() => setEditing(false)}>Cancel</button>
      </div>
      {err && <p className="error">{err}</p>}
    </div>
  );
}

const COMPLEXITY: [string, string][] = [["", "Engine default (medium)"], ["low", "Low"], ["medium", "Medium"], ["high", "High"]];

/** The estimator's pricing choices on top of the confirmed record. Empty means "let the engine decide". */
export function PricingForm({ value, onChange }: { value: PricingChoices; onChange: (c: PricingChoices) => void }) {
  return (
    <div className="pricing-form">
      <label>Complexity
        <select value={value.complexity ?? ""} onChange={(e) => onChange({ ...value, complexity: e.target.value || undefined })}>
          {COMPLEXITY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </label>
      <label>Building type for construction cost
        <select value={value.cost_type ?? ""} onChange={(e) => onChange({ ...value, cost_type: e.target.value || undefined })}>
          <option value="">Engine default (from the use)</option>
          {Object.entries(COST_TYPE).filter(([k]) => k !== "average").map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </label>
      <label>Construction cost override (USD/m²)
        <input type="number" min={0} value={value.construction_cost_usd_m2 ?? ""} placeholder="From the cost book"
               onChange={(e) => onChange({ ...value, construction_cost_usd_m2: e.target.value === "" ? undefined : Number(e.target.value) })} />
      </label>
    </div>
  );
}
