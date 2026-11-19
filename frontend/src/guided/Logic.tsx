import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Search } from "lucide-react";
import { guided, type LogicEntry, type LogicRegister } from "../api";
import { HeroCard, PinnedCard } from "./kit";
import { errorText, money } from "./ui";

const KIND: Record<LogicEntry["kind"], string> = {
  cost: "Cost book", setting: "Fee method", market: "Market", rule: "Rule", policy: "Intake policy", built_in: "Built in",
};

/** The pricing logic register: every rule and number behind a fee, in the order the engine applies them.
 * Opened from a fee card, it shows what that fee used. */
export function LogicPage({ envelopeId }: { envelopeId?: number }) {
  const [reg, setReg] = useState<LogicRegister | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [onlyUsed, setOnlyUsed] = useState(!!envelopeId);
  useEffect(() => { guided.logic(envelopeId).then(setReg, (e) => setErr(errorText(e))); }, [envelopeId]);

  const used = useMemo(() => new Set(reg?.request?.used ?? []), [reg]);
  const shown = useMemo(() => {
    const words = q.trim().toLowerCase();
    return (reg?.entries ?? []).filter((e) => (!onlyUsed || used.has(e.id)) &&
      (!words || `${e.title} ${e.value} ${e.rule} ${e.source}`.toLowerCase().includes(words)));
  }, [reg, q, onlyUsed, used]);

  if (err) return <p role="alert" className="bw-body">{err}</p>;
  if (!reg) return <p className="bw-muted">Loading…</p>;
  const r = reg.request;
  const changed = reg.entries.filter((e) => e.changed).length;

  return (
    <div className="bw-stack bw-enter" style={{ "--gap": "18px" } as React.CSSProperties}>
      <div className="bw-stack" style={{ "--gap": "6px" } as React.CSSProperties}>
        <h1 className="bw-display">How we price</h1>
        <p className="bw-lead">Every rule and number behind a fee, in the order the engine applies them. Nothing is hidden:
          what is not a setting is listed as built in.</p>
      </div>

      <HeroCard label="This register" i={0}>
        {r ? (
          <>
            <span className="bw-caps">Traced from a fee card</span>
            <h2 className="bw-title">{r.title ?? `Request ${r.envelope_id}`}</h2>
            <p className="bw-body">
              {r.priced && r.opening != null ? <>The opening fee of <strong>{money(r.opening, r.currency ?? "")}</strong> used </> : <>Gate 1 used </>}
              <strong>{r.used.length}</strong> of the {reg.entries.length} entries below.
            </p>
            <div className="bw-segmented" role="group" aria-label="Show">
              <button type="button" aria-pressed={onlyUsed} onClick={() => setOnlyUsed(true)}>What this fee used</button>
              <button type="button" aria-pressed={!onlyUsed} onClick={() => setOnlyUsed(false)}>Everything</button>
            </div>
            <a className="bw-btn bw-btn--primary" href={`#/r/${r.envelope_id}/fee`}>Back to the fee card</a>
          </>
        ) : (
          <>
            <span className="bw-caps">The register</span>
            <h2 className="bw-title">{reg.entries.length} rules and numbers</h2>
            <p className="bw-body">Fee engine {reg.engine_version}. {changed ? `${changed} changed by the practice since setup; ` : ""}
              values are edited under Set up, and every edit is logged here with who and why.</p>
            <p className="bw-muted">Open a fee card and choose “See every rule this fee used” to trace one fee.</p>
          </>
        )}
      </HeroCard>

      <label className="bw-search">
        <Search className="bw-icon bw-icon--sm" aria-hidden />
        <input className="bw-input" type="search" placeholder="Search the register: office, Qatar, stage share…"
               value={q} onChange={(e) => setQ(e.target.value)} />
      </label>

      {reg.sections.map((s, i) => {
        const items = shown.filter((e) => e.section === s.key);
        if (!items.length) return null;
        return (
          <PinnedCard key={s.key} title={s.title} i={i + 1}>
            <p className="bw-muted">{s.about}</p>
            <ul className="bw-register">
              {items.map((e) => <Entry key={e.id} e={e} used={!!r && used.has(e.id)} effects={r?.effects?.[e.id] ?? []}
                                       envelopeId={r?.envelope_id} />)}
            </ul>
          </PinnedCard>
        );
      })}
      {shown.length === 0 && <p className="bw-muted">Nothing matches “{q}”.</p>}
    </div>
  );
}

function Entry({ e, used, effects, envelopeId }: { e: LogicEntry; used: boolean; effects: string[]; envelopeId?: number }) {
  const when = (at: string) => new Date(at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  return (
    <li id={e.id}>
      <details className="bw-register__item" data-used={used || undefined}>
        <summary className="bw-register__head">
          <span className="bw-register__title">{e.title}</span>
          <span className="bw-register__value">{e.value}</span>
          <ChevronDown className="bw-icon bw-icon--sm bw-register__chev" aria-hidden />
          <span className="bw-row bw-register__meta">
            <span className="bw-badge" data-kind={e.kind}>{KIND[e.kind]}</span>
            {used && <span className="bw-badge" data-status="found">Used by this fee</span>}
            {e.changed && <span className="bw-muted">Changed {when(e.changed.at)}</span>}
          </span>
        </summary>
        <div className="bw-register__body">
          {e.rule && <p className="bw-body">{e.rule}</p>}
          <dl className="bw-doc__facts">
            <dt>Value</dt><dd>{e.value}</dd>
            <dt>Source</dt><dd>{e.source}</dd>
            <dt>Who can change it</dt>
            <dd>{e.editable ? "An owner, under Set up. Every change is logged with who and why." : "Built into the engine; changing it is a new engine version."}</dd>
          </dl>
          {used && effects.length > 0 && (
            <div className="bw-stack" style={{ "--gap": "4px" } as React.CSSProperties}>
              <span className="bw-caps">On this fee</span>
              <ul className="bw-doc__list">{effects.map((x, k) => <li key={k}>{x}</li>)}</ul>
            </div>
          )}
          <div className="bw-stack" style={{ "--gap": "4px" } as React.CSSProperties}>
            <span className="bw-caps">History</span>
            {e.history.length ? (
              <ul className="bw-doc__list">
                {e.history.map((h, k) => (
                  <li key={k}>{when(h.at)} · {h.by}: {change(h.old, h.new)}{h.reason ? ` (“${h.reason}”)` : ""}</li>
                ))}
              </ul>
            ) : <p className="bw-muted">{e.editable ? "Unchanged since the practice was set up." : "Part of fee engine; versioned with the code."}</p>}
          </div>
          <div className="bw-row">
            {e.edit && e.editable && <a className="bw-btn bw-btn--outline bw-btn--sm" href={e.edit}>Change it under Set up</a>}
            {used && envelopeId && <a className="bw-btn bw-btn--outline bw-btn--sm" href={`#/r/${envelopeId}/fee`}>See it on the fee card</a>}
          </div>
        </div>
      </details>
    </li>
  );
}

function change(old: Record<string, unknown> | null, now: Record<string, unknown> | null): string {
  const show = (o: Record<string, unknown> | null) => o ? Object.entries(o).filter(([k]) => !["org_id", "updated_at", "updated_by"].includes(k))
    .map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ") : "";
  if (!old) return `set ${show(now)}`;
  const keys = Object.keys(now ?? {});
  const was = Object.fromEntries(keys.map((k) => [k, old[k]]));
  return `${show(was)} → ${show(now)}`;
}
