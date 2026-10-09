import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Envelope } from "../api";
import { LEAD, countryName, daysLeft, formatDate } from "../format";
import { useCan } from "../session";

const STATUS: Record<Envelope["status"], string> = {
  received: "Received", reading: "Reading", extracting: "Extracting", review: "In review", confirmed: "Confirmed",
  failed: "Failed",
};

export function Inbox({ onOpen }: { onOpen: (id: number) => void }) {
  const [items, setItems] = useState<Envelope[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const can = useCan();

  const load = useCallback(() => api.inbox().then(setItems), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!items?.some((e) => ["reading", "extracting"].includes(e.status))) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [items, load]);

  const upload = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    setMsg(null);
    try {
      const r = await api.upload(files);
      setMsg(r.created ? "Pack received. Extraction has started." : "These files were already received; opened the existing envelope.");
      await load();
      if (!r.created) onOpen(r.envelope_id);
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    setUploading(false);
  };

  return (
    <div className="page inbox">
      <div className="page-head">
        <h1>RFP inbox</h1>
        {can("intake") && <div className="drop"
             onDragOver={(e) => e.preventDefault()}
             onDrop={(e) => { e.preventDefault(); upload([...e.dataTransfer.files]); }}>
          <span>Drop an RFP pack here: a zip, an email (.eml), or the individual files.</span>
          <button className="primary" disabled={uploading} onClick={() => input.current?.click()}>
            {uploading ? "Uploading…" : "Upload RFP pack"}
          </button>
          <input ref={input} type="file" multiple hidden onChange={(e) => upload([...(e.target.files ?? [])])} />
        </div>}
        {msg && <p role="status" className="status-msg">{msg}</p>}
      </div>
      {!items ? <p className="empty">Loading…</p> : !items.length ? (
        <p className="empty">{can("intake") ? "No RFPs yet. Upload the pack a client sent you to start." : "No RFPs yet. An estimator or admin can upload one."}</p>
      ) : (
        <table className="register-table">
          <thead>
            <tr><th>Project</th><th>Location</th><th>Received</th><th>Deadline</th><th>Lead status</th>
              <th>Pricing fields</th><th>Conflicts</th><th>Status</th></tr>
          </thead>
          <tbody>
            {items.map((e) => {
              const left = daysLeft(e.submission_deadline, e.received_at);
              return (
                <tr key={e.envelope_id} onClick={() => onOpen(e.envelope_id)} tabIndex={0}
                    onKeyDown={(k) => k.key === "Enter" && onOpen(e.envelope_id)}>
                  <td><strong>{e.project_name ?? e.title}</strong><br /><span className="dim">{e.documents} files via {e.channel}</span></td>
                  <td>{[e.city, countryName(e.country)].filter(Boolean).join(", ")}{e.tier && <><br /><span className="dim">{e.tier} tier, {e.billing_currency}</span></>}</td>
                  <td>{formatDate(e.received_at)}</td>
                  <td>{e.submission_deadline ? <>{formatDate(e.submission_deadline)}<br /><span className="dim">{left} days</span></> : <span className="dim">Not stated</span>}</td>
                  <td><span className={`lead lead-${e.lead_status}`}>{e.lead_status ? LEAD[e.lead_status] : ""}</span></td>
                  <td>{e.completeness != null && (
                    <span className="meter" aria-label={`${Math.round(e.completeness * 100)}%`}>
                      <span style={{ width: `${e.completeness * 100}%` }} />
                    </span>)}
                  </td>
                  <td>{e.open_conflicts ? <span className="delta-count">△ {e.open_conflicts}</span> : <span className="dim">None</span>}</td>
                  <td>{STATUS[e.status]}{e.confirmed_version ? ` (v${e.confirmed_version})` : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
