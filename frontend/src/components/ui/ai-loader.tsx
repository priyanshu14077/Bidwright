/* AILoader: a soft glowing ring with the word it is working on, letter by letter. Built for Bidwright in the
   Pin-up tokens (the original 21st.dev "ai-loader" source was not available); same import path and `Component`
   export as the 21st demo. In the chat it also says what the assistant is doing now and what it has done. */
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import "./ai-loader.css";

export interface AILoaderProps {
  /** The word that animates, e.g. "Thinking" or "Reading". */
  label?: string;
  /** What is happening now, e.g. "Checking what's missing". */
  doing?: string | null;
  /** Steps already finished, oldest first. */
  done?: string[];
  /** Seconds since it started. */
  elapsed?: number;
  /** A small line under the steps, e.g. "This takes under a minute". */
  hint?: string;
  /** Large and centred (the demo) or a compact row (the chat). */
  size?: "large" | "compact";
  className?: string;
}

export function AILoader({ label = "Generating", doing, done = [], elapsed, hint, size = "compact", className }: AILoaderProps) {
  return (
    <div className={cn("bw-ail", className)} data-size={size} role="status" aria-live="polite"
         aria-label={`${label}${doing ? `: ${doing}` : ""}`}>
      <div className="bw-ail__orb" aria-hidden="true">
        <span className="bw-ail__ring" />
        <span className="bw-ail__ring bw-ail__ring--2" />
        <span className="bw-ail__core" />
      </div>
      <div className="bw-ail__text">
        <div className="bw-ail__label" aria-hidden="true">
          {[...label].map((ch, i) => <span key={i} style={{ animationDelay: `${i * 0.09}s` }}>{ch === " " ? " " : ch}</span>)}
          {elapsed != null && <span className="bw-ail__clock">{elapsed.toFixed(1)}s</span>}
        </div>
        {(doing || done.length > 0) && (
          <ol className="bw-ail__steps">
            {done.slice(-3).map((d, i) => <li key={`${d}-${i}`} data-done><Check aria-hidden="true" />{d}</li>)}
            {doing && <li key={doing} data-now>{doing}</li>}
          </ol>
        )}
        {hint && <p className="bw-ail__hint">{hint}</p>}
      </div>
    </div>
  );
}

/** The 21st.dev demo's export: a large loader on its own. */
export function Component() {
  return <AILoader label="Generating" size="large" />;
}

export default AILoader;
