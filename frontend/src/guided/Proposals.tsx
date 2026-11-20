import { useEffect, useMemo, useState } from "react";
import { ArrowRight, FileText, Search } from "lucide-react";
import { proposals as proposalApi, type ProposalRow, type VersionStatus } from "../api";
import { HeroCard } from "./kit";
import { errorText, money } from "./ui";

const STATUS: Record<VersionStatus, [string, "found" | "estimated" | "missing" | undefined]> = {
  draft: ["Draft", "estimated"], approved: ["Approved", "found"], superseded: ["Superseded", undefined],
} as Record<VersionStatus, [string, "found" | "estimated" | "missing" | undefined]>;

/** Every proposal the practice has written in Bidwright, newest first: its fee, where it stands, and a way into
 * the proposal itself and the request it answers. */
export function Proposals() {
  const [items, setItems] = useState<ProposalRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<"all" | VersionStatus>("all");
  useEffect(() => { proposalApi.list().then(setItems, (e: unknown) => setErr(errorText(e))); }, []);

  const shown = useMemo(() => (items ?? []).filter((p) => (status === "all" || p.status === status)
    && (!q.trim() || `${p.reference} ${p.title}`.toLowerCase().includes(q.trim().toLowerCase()))), [items, q, status]);
  const count = (s: VersionStatus) => (items ?? []).filter((p) => p.status === s).length;

  return (
    <div className="bw-stack bw-enter" style={{ "--gap": "18px" } as React.CSSProperties}>
      <div className="bw-stack" style={{ "--gap": "6px" } as React.CSSProperties}>
        <h1 className="bw-display">Proposals</h1>
        <p className="bw-lead">Every proposal written from an approved fee, newest first. Open one to edit its sections, reprice it or approve and export it.</p>
      </div>

      <HeroCard label="Proposals at a glance" i={0}>
        <div className="bw-row bw-proposals__counts">
          <div><span className="bw-caps">All</span><strong>{items?.length ?? "…"}</strong></div>
          <div><span className="bw-caps">Draft</span><strong>{items ? count("draft" as VersionStatus) : "…"}</strong></div>
          <div><span className="bw-caps">Approved</span><strong>{items ? count("approved" as VersionStatus) : "…"}</strong></div>
        </div>
        <div className="bw-segmented" role="group" aria-label="Show">
          {(["all", "draft", "approved"] as const).map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s as "all" | VersionStatus)}>
              {s === "all" ? "All" : STATUS[s as VersionStatus][0]}
            </button>
          ))}
        </div>
      </HeroCard>

      <label className="bw-search">
        <Search className="bw-icon bw-icon--sm" aria-hidden />
        <input className="bw-input" type="search" placeholder="Search by reference or project…" value={q} onChange={(e) => setQ(e.target.value)} />
      </label>

      {err && <p role="alert" className="bw-body">{err}</p>}
      {items !== null && shown.length === 0 && (
        <p className="bw-muted">{items.length ? "No proposal matches." : "No proposals yet. A proposal is created when a fee is approved on a request."}</p>
      )}
      <ul className="bw-proposals" aria-label="Proposals">
        {shown.map((p, i) => {
          const st = STATUS[p.status] ?? [p.status, undefined];
          return (
            <li key={p.proposal_id} className="bw-list-item bw-proposals__item" style={{ "--i": i } as React.CSSProperties}>
              <FileText className="bw-icon bw-proposals__icon" aria-hidden />
              <div className="bw-list-item__main">
                <div className="bw-list-item__head">
                  <a className="bw-list-item__title bw-proposals__title" href={`#/proposal/${p.proposal_id}/v/${p.latest_version}`}>{p.title}</a>
                  <span className="bw-badge" data-status={st[1]}>{st[0]}</span>
                </div>
                <div className="bw-muted">{p.reference} · version {p.latest_version} · {new Date(p.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</div>
                <div className="bw-row bw-proposals__foot">
                  <strong className="bw-num">{money(Number(p.fee_total_local), p.currency)}</strong>
                  <a className="bw-push bw-proposals__req" href={`#/r/${p.envelope_id}`}>The request<ArrowRight className="bw-icon bw-icon--sm" aria-hidden /></a>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
