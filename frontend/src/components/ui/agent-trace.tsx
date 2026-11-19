/* AgentTrace: an agent run on a time axis that you can scrub. Ported from the 21st.dev component to this app's
   design system: the props, behaviour and markup are the same; the Tailwind classes became bw-trace classes
   in agent-trace.css, which use the Pin-up tokens (so it follows the light and dark themes). */
import * as React from "react";
import { Bot, CircleAlert, Cpu, FileText, Pause, Play, Wrench, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import "./agent-trace.css";

/** What produced the span. Drives the icon and whether the bar uses the accent color. */
export type SpanKind = "agent" | "model" | "tool" | "io";

/** How the span finished. `cached` means it returned without doing the work. */
export type SpanStatus = "ok" | "error" | "cached";

export interface TraceSpan {
  /** Stable id. Used for React keys and for `parentId` links. */
  id: string;
  /** Tool, model or sub-agent name, e.g. `search_docs`. Rendered in the name gutter. */
  label: string;
  /** Start offset in milliseconds from the beginning of the run. */
  start: number;
  /** End offset in milliseconds. For a span still in flight, pass the current time. */
  end: number;
  /** Defaults to `"tool"`. `agent` and `model` spans are drawn in the accent color. */
  kind?: SpanKind;
  /** Defaults to `"ok"`. `error` draws the bar in the error color. */
  status?: SpanStatus;
  /** Id of the span this one runs inside. Children are indented under their parent. */
  parentId?: string;
  /** One-line result shown on the right once the span finishes, e.g. `"12 matches"`. */
  detail?: string;
  /** Tokens billed to this span. Counts up as the span replays; shown instead of `detail`. */
  tokens?: number;
  /** Attempt number. Anything above 1 renders a `×n` retry chip next to the label. */
  attempt?: number;
}

export interface AgentTraceProps extends React.ComponentProps<"div"> {
  /** The run. Order is derived from `start` and `parentId`, so the array can be in any order. */
  spans: TraceSpan[];
  /** Total run length in milliseconds. Defaults to the largest `end` in `spans`. */
  duration?: number;
  /** Run identifier shown in the header, e.g. `run_7c41f2`. */
  runId?: string;
  /** Model or agent name shown next to the run id. */
  model?: string;
  /** Playhead position in milliseconds. Changing it seeks. Reduced motion pins it to the end of the run. */
  defaultTime?: number;
  /** Start replaying on mount. Ignored under `prefers-reduced-motion`. */
  autoPlay?: boolean;
  /** Restart after the run finishes. Off by default: a finished run is the best-looking resting state. */
  loop?: boolean;
  /** Playback rate. 1 replays the run in real time. */
  speed?: number;
  /** Pause at the end of the run before looping, in milliseconds. */
  holdMs?: number;
  /** Show the time ruler and its gridlines. */
  showRuler?: boolean;
  /** Show the play button, the scrub rail and the clock. */
  showTransport?: boolean;
  /** Show token counts for spans that carry them. */
  showTokens?: boolean;
  /** Width of the name gutter in pixels, used once the card itself is at least 448px wide. */
  labelWidth?: number;
  /** Height of one span row in pixels. */
  rowHeight?: number;
  /** Called when a row is activated. The playhead also seeks to that span's start. */
  onSpanSelect?: (span: TraceSpan) => void;
}

interface LaidSpan extends TraceSpan {
  depth: number;
  dur: number;
}

interface RowHandle {
  el: HTMLElement;
  meta: HTMLElement | null;
  dur: HTMLElement | null;
  status: HTMLElement | null;
  span: LaidSpan;
  lastP: number;
  lastState: string;
  lastMeta: string;
  lastDur: string;
}

const KIND_ICON = { agent: Bot, model: Cpu, tool: Wrench, io: FileText } as const;
const NICE_TICKS = [50, 100, 250, 500, 1000, 2000, 2500, 5000, 10_000, 30_000, 60_000];

const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);

/** Deterministic thousands separator: `toLocaleString` differs between server and client. */
const groupDigits = (n: number) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

const formatMs = (ms: number) => (ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`);

/** What the result column says: tokens when the span has them and `showTokens` is on, else `detail` once the
 *  span has finished (you do not know the answer before it returns). */
const metaText = (span: LaidSpan, progress: number, showTokens: boolean) =>
  span.tokens != null && showTokens
    ? `${groupDigits(Math.round(span.tokens * progress))} tk`
    : progress >= 1
      ? (span.detail ?? "")
      : "";

const STATUS_WORD = { queued: "queued", running: "running", done: "succeeded", error: "failed" } as const;

/** The row's status in words, for the screen-reader-only line. */
const statusWord = (span: LaidSpan, state: keyof typeof STATUS_WORD) =>
  state === "done" && span.status === "cached" ? "served from cache" : STATUS_WORD[state];

const useIsomorphicLayoutEffect = typeof window === "undefined" ? React.useEffect : React.useLayoutEffect;

/** Flatten spans into rows: children under their parent, siblings by start time; cycles and orphans tolerated. */
function layout(spans: TraceSpan[]): LaidSpan[] {
  const children = new Map<string, TraceSpan[]>();
  const ids = new Set(spans.map((s) => s.id));
  for (const s of spans) {
    const key = s.parentId && s.parentId !== s.id && ids.has(s.parentId) ? s.parentId : "";
    const list = children.get(key);
    if (list) list.push(s);
    else children.set(key, [s]);
  }
  const out: LaidSpan[] = [];
  const seen = new Set<string>();
  const walk = (parent: string, depth: number) => {
    for (const kid of (children.get(parent) ?? []).slice().sort((a, b) => a.start - b.start)) {
      if (seen.has(kid.id)) continue;
      seen.add(kid.id);
      out.push({ ...kid, depth, dur: Math.max(0, kid.end - kid.start) });
      walk(kid.id, depth + 1);
    }
  };
  walk("", 0);
  for (const s of spans) if (!seen.has(s.id)) out.push({ ...s, depth: 0, dur: Math.max(0, s.end - s.start) });
  return out;
}

interface TraceSpanRowProps {
  span: LaidSpan;
  index: number;
  total: number;
  showTokens: boolean;
  selected: boolean;
  onSelect: (span: LaidSpan) => void;
  register: (index: number, handle: RowHandle | null) => void;
}

/** One span. Renders its finished state; the playhead loop rewrites `--p`, `data-state` and the numbers. */
export const TraceSpanRow = React.memo(function TraceSpanRow({
  span, index, total, showTokens, selected, onSelect, register,
}: TraceSpanRowProps) {
  const Icon = KIND_ICON[span.kind ?? "tool"];
  const accent = span.kind === "agent" || span.kind === "model";
  const settled = span.status === "error" ? "error" : "done";
  const left = `${(span.start / total) * 100}%`;
  const width = `${(span.dur / total) * 100}%`;

  return (
    <div
      ref={(node) => {
        register(index, node ? {
          el: node,
          meta: node.querySelector<HTMLElement>("[data-part='meta']"),
          dur: node.querySelector<HTMLElement>("[data-part='dur']"),
          status: node.querySelector<HTMLElement>("[data-part='status']"),
          span, lastP: 1, lastState: settled, lastMeta: metaText(span, 1, showTokens), lastDur: formatMs(span.dur),
        } : null);
      }}
      data-slot="trace-span"
      data-state={settled}
      data-selected={selected ? "true" : undefined}
      data-accent={accent || undefined}
      data-status={span.status ?? "ok"}
      style={{ "--p": 1 } as React.CSSProperties}
      className="bw-trace__row"
    >
      {span.depth > 0 && (
        <div aria-hidden="true" className="bw-trace__guides">
          {Array.from({ length: span.depth }, (_, d) => <span key={d} style={{ left: `${d * 12 + 11}px` }} />)}
        </div>
      )}

      <span data-part="status" className="bw-sr-only">{statusWord(span, settled)}</span>

      <button
        type="button"
        data-slot="trace-span-label"
        onClick={() => onSelect(span)}
        aria-label={`${span.label}, ${span.kind ?? "tool"}, ${formatMs(span.dur)}${
          span.attempt != null && span.attempt > 1 ? `, attempt ${span.attempt}` : ""}`}
        className="bw-trace__label"
        style={{ paddingLeft: `${span.depth * 12 + 4}px` }}
      >
        <Icon aria-hidden="true" className="bw-trace__icon" />
        <span className="bw-trace__name">{span.label}</span>
        {span.attempt != null && span.attempt > 1 && <span className="bw-trace__retry">×{span.attempt}</span>}
      </button>

      <div className="bw-trace__lane">
        <div className="bw-trace__bar" style={{ left, width }}>
          <span aria-hidden="true" className="bw-trace__ghost" />
          <span aria-hidden="true" className="bw-trace__fill" style={{ transform: "scaleX(var(--p,1))" }} />
        </div>
      </div>

      <div className="bw-trace__result">
        {span.status === "error" && <CircleAlert aria-hidden="true" className="bw-trace__err-icon" />}
        {span.status === "cached" && <Zap aria-hidden="true" className="bw-trace__cache-icon" />}
        <span data-part="meta" className="bw-trace__meta">{metaText(span, 1, showTokens)}</span>
        <span data-part="dur" className="bw-trace__dur">{formatMs(span.dur)}</span>
      </div>
    </div>
  );
});

/**
 * AgentTrace: drag the timeline (or the rail below it) and the run replays: bars fill, statuses flip from
 * queued to running to done, token counts tick up, and scrubbing backwards un-runs it. Every visual is derived
 * from one playhead time, so the component keeps no per-span state and writes one CSS variable per row per frame.
 */
export function AgentTrace({
  spans, duration, runId = "run", model, defaultTime = 0, autoPlay = true, loop = false, speed = 1, holdMs = 900,
  showRuler = true, showTransport = true, showTokens = true, labelWidth = 200, rowHeight = 34, onSpanSelect,
  className, style, ref, ...rest
}: AgentTraceProps) {
  const rootRef = React.useRef<HTMLDivElement>(null);
  const railRef = React.useRef<HTMLDivElement>(null);
  const clockRef = React.useRef<HTMLSpanElement>(null);
  const statusRef = React.useRef<HTMLSpanElement>(null);
  const rowsRef = React.useRef<(RowHandle | null)[]>([]);
  const timeRef = React.useRef(defaultTime);
  const playingRef = React.useRef(false);
  const seekRef = React.useRef<(ms: number) => void>(() => {});
  const runRef = React.useRef<() => void>(() => {});
  const haltRef = React.useRef<() => void>(() => {});
  const [playing, setPlaying] = React.useState(false);
  const [selected, setSelected] = React.useState<string | null>(null);

  const rows = React.useMemo(() => layout(spans), [spans]);
  const total = React.useMemo(() => Math.max(1, duration ?? rows.reduce((max, s) => Math.max(max, s.end), 0)), [rows, duration]);
  const ticks = React.useMemo(() => {
    const step = NICE_TICKS.find((t) => total / t <= 8) ?? total / 4;
    const out: number[] = [];
    for (let t = step; t < total; t += step) out.push(t);
    return out;
  }, [total]);

  const opts = React.useRef({ loop, speed, holdMs, showTokens, total });
  useIsomorphicLayoutEffect(() => {
    opts.current = { loop, speed, holdMs, showTokens, total };
  });

  const registerRow = React.useCallback((index: number, handle: RowHandle | null) => {
    rowsRef.current[index] = handle;
  }, []);

  useIsomorphicLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    let raf = 0;
    let last = 0;
    let holdUntil = 0;
    let onScreen = true;
    let ariaAt = -1;
    let lastT = "";
    let lastRun = "";

    const paint = () => {
      const t = timeRef.current;
      const o = opts.current;
      const ratio = (t / o.total).toFixed(5);
      if (ratio !== lastT) {
        root.style.setProperty("--t", ratio);
        lastT = ratio;
      }
      for (const row of rowsRef.current) {
        if (!row) continue;
        const { span } = row;
        const p = span.dur > 0 ? clamp((t - span.start) / span.dur, 0, 1) : t >= span.start ? 1 : 0;
        if (p !== row.lastP) {
          row.el.style.setProperty("--p", p.toFixed(4));
          row.lastP = p;
        }
        const state = t < span.start ? "queued" : p < 1 ? "running" : span.status === "error" ? "error" : "done";
        if (state !== row.lastState) {
          row.el.dataset.state = state;
          if (row.status) row.status.textContent = statusWord(span, state);
          row.lastState = state;
        }
        const dur = state === "queued" ? "" : formatMs(span.dur * p);
        if (row.dur && dur !== row.lastDur) {
          row.dur.textContent = dur;
          row.lastDur = dur;
        }
        const meta = state === "queued" ? "" : metaText(span, p, o.showTokens);
        if (row.meta && meta !== row.lastMeta) {
          row.meta.textContent = meta;
          row.lastMeta = meta;
        }
      }
      if (clockRef.current) clockRef.current.textContent = `${(t / 1000).toFixed(2)}s / ${(o.total / 1000).toFixed(2)}s`;
      const runState = t >= o.total ? "complete" : "running";
      if (runState !== lastRun) {
        root.dataset.run = runState;
        if (statusRef.current) statusRef.current.textContent = runState === "complete" ? "Completed" : "Running";
        lastRun = runState;
      }
      const rail = playingRef.current ? null : railRef.current;
      const decis = Math.round(t / 100);
      if (rail && decis !== ariaAt) {
        ariaAt = decis;
        rail.setAttribute("aria-valuenow", String(Math.round(t)));
        rail.setAttribute("aria-valuetext", `${formatMs(t)} of ${formatMs(o.total)}`);
      }
    };

    const frame = (now: number) => {
      raf = 0;
      const o = opts.current;
      const dt = Math.min(now - (last || now), 100);
      last = now;
      if (holdUntil > 0) {
        if (now >= holdUntil) {
          holdUntil = 0;
          timeRef.current = 0;
        }
      } else {
        timeRef.current += dt * o.speed;
        if (timeRef.current >= o.total) {
          timeRef.current = o.total;
          if (o.loop) holdUntil = now + o.holdMs;
          else {
            playingRef.current = false;
            setPlaying(false);
          }
        }
      }
      paint();
      if (playingRef.current) raf = requestAnimationFrame(frame);
    };

    const start = () => {
      if (raf || !playingRef.current || !onScreen || document.hidden) return;
      last = 0;
      raf = requestAnimationFrame(frame);
    };
    const halt = () => {
      cancelAnimationFrame(raf);
      raf = 0;
    };

    runRef.current = start;
    haltRef.current = halt;
    seekRef.current = (ms: number) => {
      timeRef.current = clamp(ms, 0, opts.current.total);
      holdUntil = 0;
      last = 0;
      paint();
    };

    if (media.matches) {
      timeRef.current = opts.current.total;
    } else if (autoPlay) {
      playingRef.current = true;
      setPlaying(true);
    }
    paint();

    const onVisibility = () => (document.hidden ? halt() : start());
    const onMedia = () => {
      if (!media.matches) return;
      playingRef.current = false;
      setPlaying(false);
      halt();
      seekRef.current(opts.current.total);
    };
    const io = new IntersectionObserver(([entry]) => {
      onScreen = entry?.isIntersecting ?? true;
      if (onScreen) start();
      else halt();
    }, { threshold: 0 });

    io.observe(root);
    document.addEventListener("visibilitychange", onVisibility);
    media.addEventListener("change", onMedia);
    start();

    return () => {
      halt();
      io.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      media.removeEventListener("change", onMedia);
      runRef.current = () => {};
      haltRef.current = () => {};
      seekRef.current = () => {};
    };
  }, [autoPlay]);

  useIsomorphicLayoutEffect(() => {
    rowsRef.current.length = rows.length;
    seekRef.current(timeRef.current);
  }, [rows, total, showTokens, rowHeight, labelWidth]);

  const appliedDefault = React.useRef(defaultTime);
  useIsomorphicLayoutEffect(() => {
    if (appliedDefault.current === defaultTime) return;
    appliedDefault.current = defaultTime;
    seekRef.current(defaultTime);
  }, [defaultTime]);

  const togglePlay = () => {
    const next = !playingRef.current;
    if (next && timeRef.current >= total) timeRef.current = 0;
    playingRef.current = next;
    setPlaying(next);
    seekRef.current(timeRef.current);
    if (next) runRef.current();
    else haltRef.current();
  };

  const scrubFrom = (clientX: number, el: HTMLElement) => {
    const inset = Number(el.dataset.inset ?? 0);
    const rect = el.getBoundingClientRect();
    const width = rect.width - inset * 2;
    if (width > 0) seekRef.current(((clientX - rect.left - inset) / width) * total);
  };
  const onScrubDown = (e: React.PointerEvent<HTMLElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    scrubFrom(e.clientX, e.currentTarget);
  };
  const onScrubMove = (e: React.PointerEvent<HTMLElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) scrubFrom(e.clientX, e.currentTarget);
  };
  const onScrubUp = (e: React.PointerEvent<HTMLElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  };

  const onRailKeyDown = (e: React.KeyboardEvent) => {
    const step = total / 50;
    const delta: Record<string, number> = {
      ArrowRight: step, ArrowUp: step, ArrowLeft: -step, ArrowDown: -step, PageUp: step * 10, PageDown: step * -10,
    };
    if (e.key === "Home") seekRef.current(0);
    else if (e.key === "End") seekRef.current(total);
    else if (e.key in delta) seekRef.current(timeRef.current + (delta[e.key] ?? 0));
    else return;
    e.preventDefault();
  };

  const onSpanSelectRef = React.useRef(onSpanSelect);
  onSpanSelectRef.current = onSpanSelect;
  const selectSpan = React.useCallback((span: LaidSpan) => {
    setSelected(span.id);
    seekRef.current(span.start);
    onSpanSelectRef.current?.(span);
  }, []);

  return (
    <div
      ref={(node) => {
        rootRef.current = node;
        if (typeof ref === "function") ref(node);
        else if (ref) ref.current = node;
      }}
      data-slot="agent-trace"
      style={{ "--label-w": `${labelWidth}px`, "--row": `${rowHeight}px`, ...style } as React.CSSProperties}
      className={cn("bw-trace", className)}
      {...rest}
    >
      <div data-slot="trace-header" className="bw-trace__header">
        <span aria-hidden="true" className="bw-trace__pulse" />
        <span className="bw-trace__run">{runId}</span>
        <span className="bw-trace__model">{model ? `${model} · ` : ""}{rows.length} steps</span>
        <span ref={statusRef} className="bw-trace__state">Completed</span>
      </div>

      <div className="bw-trace__body">
        {showRuler && (
          <div data-slot="trace-ruler" onPointerDown={onScrubDown} onPointerMove={onScrubMove} onPointerUp={onScrubUp}
               className="bw-trace__ruler">
            <div className="bw-trace__track">
              {ticks.map((t) => (
                <span key={t} className="bw-trace__tick" style={{ left: `${(t / total) * 100}%` }}>
                  {t < 1000 ? `${t}ms` : `${t / 1000}s`}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="bw-trace__rows">
          {showRuler && (
            <div aria-hidden="true" className="bw-trace__track bw-trace__grid">
              {ticks.map((t) => <span key={t} style={{ left: `${(t / total) * 100}%` }} />)}
            </div>
          )}

          <ol aria-label={`Steps in ${runId}`} className="bw-trace__list">
            {rows.map((span, i) => (
              <li key={span.id}>
                <TraceSpanRow span={span} index={i} total={total} showTokens={showTokens}
                              selected={selected === span.id} onSelect={selectSpan} register={registerRow} />
              </li>
            ))}
          </ol>

          <div aria-hidden="true" className="bw-trace__track bw-trace__playhead-box">
            <div className="bw-trace__playhead" style={{ transform: "translateX(calc(var(--t,0) * 100%))" }}>
              <span className="bw-trace__playhead-line" />
              <span className="bw-trace__playhead-cap" />
            </div>
          </div>

          <div data-slot="trace-scrub" onPointerDown={onScrubDown} onPointerMove={onScrubMove} onPointerUp={onScrubUp}
               className="bw-trace__track bw-trace__scrub" />
        </div>
      </div>

      {showTransport && (
        <div className="bw-trace__transport">
          <button type="button" data-slot="cta-primary" onClick={togglePlay}
                  aria-label={playing ? "Pause replay" : "Play replay"} className="bw-trace__play">
            {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" className="bw-trace__play-icon" />}
          </button>

          <div ref={railRef} role="slider" tabIndex={0} aria-label="Playhead" aria-valuemin={0}
               aria-valuemax={Math.round(total)} aria-valuenow={Math.round(clamp(defaultTime, 0, total))} data-inset="6"
               onPointerDown={onScrubDown} onPointerMove={onScrubMove} onPointerUp={onScrubUp} onKeyDown={onRailKeyDown}
               className="bw-trace__rail">
            <div className="bw-trace__rail-track">
              <div aria-hidden="true" className="bw-trace__rail-fill" style={{ transform: "scaleX(var(--t,0))" }} />
            </div>
            <div aria-hidden="true" className="bw-trace__rail-thumb-box">
              <div style={{ transform: "translateX(calc(var(--t,0) * 100%))" }}>
                <span className="bw-trace__rail-thumb" />
              </div>
            </div>
          </div>

          <span ref={clockRef} className="bw-trace__clock" />
        </div>
      )}
    </div>
  );
}

export default AgentTrace;
