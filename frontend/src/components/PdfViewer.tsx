import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { Rect } from "../api";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export type Highlight = { documentId: number; page: number; rects: Rect[]; tone: "source" | "conflict" };

type Props = { url: string; documentId: number; highlight: Highlight | null; width: number };

export function PdfViewer({ url, documentId, highlight, width }: Props) {
  const [pages, setPages] = useState<{ canvas: HTMLCanvasElement; scale: number; height: number }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pageRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    let cancelled = false;
    setPages([]);
    setError(null);
    (async () => {
      try {
        const doc = await pdfjs.getDocument({ url }).promise;
        const out = [];
        for (let n = 1; n <= doc.numPages; n++) {
          const page = await doc.getPage(n);
          const base = page.getViewport({ scale: 1 });
          const scale = width / base.width;
          const ratio = window.devicePixelRatio || 1;
          const viewport = page.getViewport({ scale: scale * ratio });
          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.style.width = `${width}px`;
          canvas.style.height = `${viewport.height / ratio}px`;
          await page.render({ canvas, viewport }).promise;
          out.push({ canvas, scale, height: viewport.height / ratio });
        }
        if (!cancelled) setPages(out);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [url, width]);

  useEffect(() => {
    if (!highlight || highlight.documentId !== documentId || !pages.length) return;
    const el = pageRefs.current[highlight.page - 1];
    const page = pages[highlight.page - 1];
    if (!el || !page || !highlight.rects.length) return;
    const top = Math.min(...highlight.rects.map((r) => r[1])) * page.scale;
    el.parentElement?.parentElement?.scrollTo({ top: el.offsetTop + top - 140, behavior: "smooth" });
  }, [highlight, pages, documentId]);

  if (error) return <p className="empty">This document could not be displayed: {error}</p>;
  if (!pages.length) return <p className="empty">Opening document…</p>;

  return (
    <div className="pdf">
      {pages.map((p, i) => (
        <div key={i} className="pdf-page" ref={(el) => { pageRefs.current[i] = el; }} style={{ height: p.height }}>
          <CanvasHost canvas={p.canvas} />
          <span className="pdf-page-no">Page {i + 1}</span>
          {highlight && highlight.documentId === documentId && highlight.page === i + 1 && (
            <svg className={`cloud cloud-${highlight.tone}`} width={width} height={p.height} aria-hidden="true">
              {highlight.rects.map((r, j) => (
                <path key={`${highlight.page}-${j}-${r.join()}`} d={cloudPath([r[0] * p.scale, r[1] * p.scale, r[2] * p.scale, r[3] * p.scale])} />
              ))}
            </svg>
          )}
        </div>
      ))}
    </div>
  );
}

function CanvasHost({ canvas }: { canvas: HTMLCanvasElement }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = ref.current;
    if (!host) return;
    host.replaceChildren(canvas);
  }, [canvas]);
  return <div ref={ref} />;
}

/** A revision cloud: the scalloped outline architects draw around a change on a drawing. */
export function cloudPath([x0, y0, x1, y1]: Rect, pad = 5, bump = 7): string {
  const left = x0 - pad, top = y0 - pad, right = x1 + pad, bottom = y1 + pad;
  const corners: [number, number][] = [[left, top], [right, top], [right, bottom], [left, bottom], [left, top]];
  let d = `M ${left} ${top}`;
  for (let i = 0; i < 4; i++) {
    const [ax, ay] = corners[i];
    const [bx, by] = corners[i + 1];
    const length = Math.hypot(bx - ax, by - ay);
    const steps = Math.max(1, Math.round(length / (bump * 2)));
    for (let s = 1; s <= steps; s++) {
      const x = ax + ((bx - ax) * s) / steps;
      const y = ay + ((by - ay) * s) / steps;
      const r = length / steps / 2;
      d += ` A ${r} ${r} 0 0 1 ${x.toFixed(1)} ${y.toFixed(1)}`;
    }
  }
  return d;
}
