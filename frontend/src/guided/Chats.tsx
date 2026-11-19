import { useEffect, useMemo, useState } from "react";
import { Archive, ArchiveRestore, MessageCircle, Pencil, Plus, Search } from "lucide-react";
import { chat, type ChatSummary } from "../api";
import { useCan } from "../session";
import { HeroCard } from "./kit";
import { errorText } from "./ui";

const RELATION: Record<ChatSummary["requests"][number]["relation"], string> = {
  created: "Read in", opened_from: "Opened from", discussed: "Discussed", decided: "Decided",
};

/** Past conversations: yours, most recent first (owners and admins can switch to everyone's), with the
 * requests each one touched. Opening one continues it on the Ask screen. */
export function Chats() {
  const can = useCan();
  const [scope, setScope] = useState<"mine" | "everyone">("mine");
  const [archived, setArchived] = useState(false);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<ChatSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => chat.list({ scope, archived, q: q.trim() || undefined }).then(setItems, (e) => setErr(errorText(e)));
  useEffect(() => {
    const t = setTimeout(load, q ? 250 : 0);  // searching waits for a pause in typing
    return () => clearTimeout(t);
  }, [scope, archived, q]);  // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => group(items ?? []), [items]);
  const change = async (c: ChatSummary, body: { title?: string; archived?: boolean }) => {
    try { await chat.change(c.thread_id, body); await load(); } catch (e) { setErr(errorText(e)); }
  };

  return (
    <div className="bw-stack bw-enter" style={{ "--gap": "18px" } as React.CSSProperties}>
      <div className="bw-stack" style={{ "--gap": "6px" } as React.CSSProperties}>
        <h1 className="bw-display">Chats</h1>
        <p className="bw-lead">Every conversation with the assistant, with the requests it read, discussed or decided. Open one to carry on.</p>
      </div>

      <HeroCard label="Start a conversation" i={0}>
        <div className="bw-row">
          <div className="bw-stack bw-grow" style={{ "--gap": "4px" } as React.CSSProperties}>
            <h2 className="bw-title">{items === null ? "Your conversations" : `${items.length} conversation${items.length === 1 ? "" : "s"}${archived ? " archived" : ""}`}</h2>
            <p className="bw-muted">Archived chats are hidden here but kept: decisions made in them stay on the record.</p>
          </div>
          <a className="bw-btn bw-btn--primary" href="#/"><Plus className="bw-icon" />New chat</a>
        </div>
        <div className="bw-row">
          {can("members") && (
            <div className="bw-segmented" role="group" aria-label="Whose">
              <button type="button" aria-pressed={scope === "mine"} onClick={() => setScope("mine")}>Mine</button>
              <button type="button" aria-pressed={scope === "everyone"} onClick={() => setScope("everyone")}>Everyone's</button>
            </div>
          )}
          <div className="bw-segmented" role="group" aria-label="Show">
            <button type="button" aria-pressed={!archived} onClick={() => setArchived(false)}>Open</button>
            <button type="button" aria-pressed={archived} onClick={() => setArchived(true)}>Archived</button>
          </div>
        </div>
      </HeroCard>

      <label className="bw-search">
        <Search className="bw-icon bw-icon--sm" aria-hidden />
        <input className="bw-input" type="search" placeholder="Search titles and messages…" value={q} onChange={(e) => setQ(e.target.value)} />
      </label>

      {err && <p role="alert" className="bw-body">{err}</p>}
      {items !== null && items.length === 0 && (
        <p className="bw-muted">{q ? `No conversation mentions “${q}”.` : archived ? "Nothing archived." : "No conversations yet. Ask something on the Ask screen and it will be kept here."}</p>
      )}
      {groups.map(([label, list], gi) => (
        <section key={label} className="bw-card bw-chats" aria-label={label} style={{ "--i": gi + 1 } as React.CSSProperties}>
          <h2 className="bw-caps">{label}</h2>
          <ul className="bw-chats__list">
            {list.map((c) => <Row key={c.thread_id} c={c} showOwner={scope === "everyone"} onChange={(b) => change(c, b)} />)}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Row({ c, showOwner, onChange }: { c: ChatSummary; showOwner: boolean; onChange: (b: { title?: string; archived?: boolean }) => void }) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(c.title ?? "");
  const save = () => { setRenaming(false); if (name.trim() && name.trim() !== c.title) onChange({ title: name.trim() }); };
  return (
    <li className="bw-chats__item">
      <MessageCircle className="bw-icon bw-chats__icon" aria-hidden />
      <div className="bw-chats__main">
        {renaming ? (
          <input className="bw-input bw-chats__rename" value={name} autoFocus aria-label="Chat title"
                 onChange={(e) => setName(e.target.value)} onBlur={save}
                 onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") { setName(c.title ?? ""); setRenaming(false); } }} />
        ) : (
          <a className="bw-chats__title" href={`#/chat/${c.thread_id}`}>{c.title ?? "Untitled conversation"}</a>
        )}
        {c.preview && <p className="bw-chats__preview">{c.preview}</p>}
        <div className="bw-row bw-chats__meta">
          <span>{when(c.updated_at)}</span>
          <span>· {c.messages} message{c.messages === 1 ? "" : "s"}</span>
          {showOwner && <span>· {c.mine ? "You" : c.owner}</span>}
          {c.requests.map((r) => (
            <a key={r.envelope_id} className="bw-badge bw-chats__req" href={`#/r/${r.envelope_id}`}
               data-status={r.relation === "decided" ? "found" : undefined}>
              {RELATION[r.relation]}: {r.title ?? `Request ${r.envelope_id}`}
            </a>
          ))}
        </div>
      </div>
      {c.mine && (
        <div className="bw-chats__actions">
          <button type="button" className="bw-icon-btn" aria-label="Rename" title="Rename" onClick={() => setRenaming(true)}>
            <Pencil className="bw-icon bw-icon--sm" />
          </button>
          <button type="button" className="bw-icon-btn" aria-label={c.archived_at ? "Restore" : "Archive"} title={c.archived_at ? "Restore" : "Archive"}
                  onClick={() => onChange({ archived: !c.archived_at })}>
            {c.archived_at ? <ArchiveRestore className="bw-icon bw-icon--sm" /> : <Archive className="bw-icon bw-icon--sm" />}
          </button>
        </div>
      )}
    </li>
  );
}

function when(at: string): string {
  const d = new Date(at);
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return dayLabel(d) === "Today" ? time : `${d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}, ${time}`;
}

function dayLabel(d: Date): string {
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((start(new Date()) - start(d)) / 86_400_000);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? "This week" : "Earlier";
}

function group(items: ChatSummary[]): [string, ChatSummary[]][] {
  const out = new Map<string, ChatSummary[]>();
  for (const c of items) {
    const k = dayLabel(new Date(c.updated_at));
    out.set(k, [...(out.get(k) ?? []), c]);
  }
  return [...out];
}
