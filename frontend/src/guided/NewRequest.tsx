import { useRef, useState } from "react";
import { FileUp, Image, Mail, MessageCircle, NotebookPen } from "lucide-react";
import { guided, type Channel } from "../api";
import { errorText } from "./ui";

const CHANNELS: { value: Channel; label: string; icon: React.ReactNode }[] = [
  { value: "whatsapp", label: "WhatsApp", icon: <MessageCircle className="bw-icon bw-icon--sm" /> },
  { value: "email", label: "Email", icon: <Mail className="bw-icon bw-icon--sm" /> },
  { value: "notes", label: "Call notes", icon: <NotebookPen className="bw-icon bw-icon--sm" /> },
];

/** One box that takes anything. The only decision is "Read request". */
export function NewRequest() {
  const [text, setText] = useState("");
  const [channel, setChannel] = useState<Channel>("whatsapp");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const pick = useRef<HTMLInputElement>(null);
  const shots = useRef<HTMLInputElement>(null);

  const read = async () => {
    setBusy(true);
    setErr(null);
    try {
      let id: number;
      if (files.length) {
        id = (await guided.upload(files, channel === "whatsapp" && !text ? "whatsapp" : channel)).envelope_id;
        if (text.trim()) await guided.reply(id, { text, channel });
      } else {
        id = (await guided.paste({ text, channel })).envelope_id;
      }
      setText("");  // the box is empty the next time it opens
      setFiles([]);
      location.hash = `#/r/${id}`;
    } catch (e) {
      setErr(errorText(e));
      setBusy(false);
    }
  };
  const add = (list: FileList | null) => list && setFiles((f) => [...f, ...Array.from(list)]);

  return (
    <>
      <div className="bw-stack">
        <h1 className="bw-display">Drop in a request</h1>
        <p className="bw-lead">Paste a message, forward an email or add files. We'll read it and tell you what's missing.</p>
      </div>
      <section className="bw-card bw-card--hero">
        <div className="bw-chips" role="group" aria-label="Where it came from">
          {CHANNELS.map((c) => (
            <button key={c.value} type="button" className="bw-chip" aria-pressed={channel === c.value}
                    onClick={() => setChannel(c.value)}>{c.icon}{c.label}</button>
          ))}
        </div>
        <label className="bw-stack" style={{ "--gap": "6px" } as React.CSSProperties}>
          <span className="bw-label">Request</span>
          <textarea className="bw-textarea" rows={6} autoComplete="off" value={text} onChange={(e) => setText(e.target.value)}
                    placeholder="e.g. Hi, Marina Crest again, they want concept for a resi tower in JVC, ~30 floors…" />
        </label>
        <div className={`bw-dropzone bw-desktop-only`} data-active={over || undefined}
             onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
             onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files); }}>
          <FileUp className="bw-icon" aria-hidden />
          <span className="bw-body">Drop an RFP pack, emails (.eml), a zip or screenshots here, or
            <button type="button" className="bw-linkish" onClick={() => pick.current?.click()}>choose files</button>.</span>
        </div>
        <div className="bw-chips bw-mobile-only">
          <button type="button" className="bw-chip" onClick={() => pick.current?.click()}><FileUp className="bw-icon bw-icon--sm" />Files</button>
          <button type="button" className="bw-chip" onClick={() => shots.current?.click()}><Image className="bw-icon bw-icon--sm" />Screenshot</button>
        </div>
        <input ref={pick} type="file" multiple hidden onChange={(e) => add(e.target.files)} />
        <input ref={shots} type="file" accept="image/*" multiple hidden onChange={(e) => add(e.target.files)} />
        {files.length > 0 && (
          <ul className="bw-stack bw-files" style={{ "--gap": "4px" } as React.CSSProperties}>
            {files.map((f, i) => (
              <li key={i} className="bw-row bw-body">{f.name}
                <button type="button" className="bw-linkish bw-push" onClick={() => setFiles(files.filter((_, j) => j !== i))}>Remove</button>
              </li>
            ))}
          </ul>
        )}
        {err && <p role="alert" className="bw-body">{err}</p>}
        <button type="button" className="bw-btn bw-btn--primary bw-btn--lg bw-btn--block" disabled={busy || (!text.trim() && !files.length)}
                onClick={read}>{busy ? "Reading…" : "Read request"}</button>
      </section>
    </>
  );
}
