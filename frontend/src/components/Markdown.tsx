import { Fragment, type ReactNode } from "react";

/** The Markdown our own drafting code writes: ### headings, **bold**, "- " bullets, pipe tables and paragraphs.
 *  Gaps marked [to be written] are drawn in revision red. */
export function Markdown({ text }: { text: string }) {
  const blocks = text.replace(/\r\n/g, "\n").split(/\n{2,}/);
  return <div className="md">{blocks.map((b, i) => <Block key={i} block={b.trim()} />)}</div>;
}

function Block({ block }: { block: string }) {
  if (!block) return null;
  const lines = block.split("\n");
  const heading = block.match(/^(#{2,4})\s+(.*)$/);
  if (heading && lines.length === 1) return <h3 className={`md-h${heading[1].length}`}>{inline(heading[2])}</h3>;
  if (lines.every((l) => l.trim().startsWith("|"))) {
    const rows = lines.filter((l) => !/^\|\s*-/.test(l.trim())).map(cells);
    const [head, ...body] = rows;
    return (
      <table className="md-table">
        <thead><tr>{head.map((c, i) => <th key={i}>{inline(c)}</th>)}</tr></thead>
        <tbody>{body.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
      </table>
    );
  }
  if (lines.every((l) => l.startsWith("- "))) return <ul>{lines.map((l, i) => <li key={i}>{inline(l.slice(2))}</li>)}</ul>;
  return <p>{lines.map((l, i) => <Fragment key={i}>{i > 0 && <br />}{inline(l)}</Fragment>)}</p>;
}

const cells = (line: string) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

function inline(s: string): ReactNode[] {
  return s.split(/(\*\*[^*]+\*\*|\[to be written\])/g).filter(Boolean).map((part, i) =>
    part === "[to be written]" ? <mark key={i} className="gap">to be written</mark>
      : part.startsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>);
}
