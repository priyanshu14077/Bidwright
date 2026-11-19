/* The one place engine data becomes UI states. Every screen asks this file how to show a value, so the
   semantics cannot drift: a source pill always says where a value came from, never a category.

     quote (the value is in the words)             → quoted     WhatsApp · “concept for a resi tower”
     inferred from a quote (the words imply it)    → derived    “JVC” → Dubai
     from the archive (client history)             → derived    Archive · 3 past proposals
     from comparable projects                      → estimated  3 UAE projects · low
     from the practice's settings                  → estimated  Practice default · medium
     from a project the client names               → estimated  As Bay Square · high
     nothing                                       → missing    (a question instead of a pill)
*/
import type { Confidence, Evidence, GateField } from "../api";
import { countryName } from "../format";

export type UiStatus = "found" | "estimated" | "missing";

export type SourceSpec =
  | { kind: "quoted"; channel: string; quote: string }
  | { kind: "derived"; quote: string; result: string }
  | { kind: "derived"; from: "Archive" | "Entered by hand" | "Fee engine"; result: string }
  | { kind: "estimated"; count: number; scope: string; confidence: Confidence }
  | { kind: "estimated"; setting: string; confidence: Confidence }
  | { kind: "estimated"; project: string; confidence: Confidence };

export type FieldState = { status: UiStatus; value: string; range: string | null; source: SourceSpec | null;
  evidence: Evidence[]; confidence: Confidence | null; note: string | null };

const CHANNEL: Record<string, string> = { whatsapp: "WhatsApp", email: "Email", notes: "Call notes", upload: "Upload" };

const words = (s: string, n = 6) => s.trim().split(/\s+/).slice(0, n).join(" ");

/** True when the value can be read straight off the quote (it shares a word with it). */
function literal(quote: string, value: string): boolean {
  const q = quote.toLowerCase();
  if (/\b(today|tomorrow|next|week|month|end of)\b/.test(q) && !/\d/.test(q)) return false;  // relative dates are worked out
  return value.toLowerCase().split(/[\s,()/·]+/).some((w) => w.length >= 3 && q.includes(w)) || /\d/.test(q) && /\d/.test(value);
}

function scopeName(scope: { level?: string; name?: string | null } | undefined): string {
  if (!scope || scope.level === "all" || !scope.name) return "";
  return scope.level === "country" ? countryName(scope.name) : scope.name;
}

/** Where one piece of evidence says a value came from. */
export function sourceOf(e: Evidence, value = "", confidence: Confidence | null = null): SourceSpec | null {
  const ref = (e.ref ?? {}) as { phrase?: string; result?: string; count?: number; archive?: boolean; setting?: string;
    scope?: { level?: string; name?: string | null }; confidence?: Confidence; referenced?: boolean; comparables?: string[] };
  const conf = (ref.confidence ?? confidence ?? "medium") as Confidence;
  if (e.kind === "text" || e.kind === "reply") {
    if (!e.quote) return e.kind === "reply" ? { kind: "derived", from: "Entered by hand", result: value || "edited" } : null;
    if (value && !literal(e.quote, value)) return { kind: "derived", quote: words(e.quote, 4), result: value };
    const channel = e.kind === "reply" ? "Client's reply" : CHANNEL[e.channel ?? ""] ?? e.file_name ?? "Request";
    return { kind: "quoted", channel, quote: words(ref.phrase ?? e.quote) };
  }
  if (e.kind === "engine") {
    if (ref.archive) return { kind: "derived", from: "Archive", result: ref.result === "New" ? "no past proposals" : value };
    if (e.quote) return { kind: "derived", quote: words(e.quote, 4), result: value || ref.result || "" };
    return null;
  }
  if (e.kind === "comparable") {
    if (ref.archive) return { kind: "derived", from: "Archive", result: `${ref.count ?? 0} past proposal${ref.count === 1 ? "" : "s"}` };
    if (ref.referenced && ref.scope?.name) return { kind: "estimated", project: ref.scope.name, confidence: conf };
    return { kind: "estimated", count: ref.count ?? ref.comparables?.length ?? 0, scope: scopeName(ref.scope), confidence: conf };
  }
  if (e.kind === "setting") return { kind: "estimated", setting: ref.setting ?? "Practice default", confidence: conf };
  return null;  // rules are reasons, not sources of a value
}

/** How a gate 1 field shows: its status, value without status marks, and its one source. */
export function fieldState(f: GateField): FieldState {
  const status: UiStatus = f.status === "found" ? "found" : f.status === "missing" ? "missing" : "estimated";
  const value = status === "missing" ? "" : displayValue(f);
  const confidence = f.estimate?.confidence ?? null;
  const first = f.evidence.map((e) => sourceOf(e, value, confidence)).find(Boolean) ?? null;
  return { status, value, range: f.range_display ?? null, source: status === "missing" ? null : first,
           evidence: f.evidence, confidence, note: f.status === "ambiguous" ? f.note : null };
}

function displayValue(f: GateField): string {
  if (f.name === "country") return countryName(String(f.value ?? "")) || f.display;
  if (f.name === "client_status") return f.value === "repeat" ? "Repeat" : f.value === "new" ? "New" : f.display;
  return f.display.replace(/^≈\s*/, "");
}

/** The words on a pill. Only structured sources are accepted, so a pill can never carry a free-text label. */
export function pillText(s: SourceSpec): string {
  if (s.kind === "quoted") return `${s.channel} · “${s.quote}”`;
  if (s.kind === "derived") return "quote" in s ? `“${s.quote}” → ${s.result}` : `${s.from} · ${s.result}`;
  if ("count" in s) return `${s.count} ${s.scope ? `${s.scope} ` : ""}project${s.count === 1 ? "" : "s"} · ${s.confidence}`;
  if ("project" in s) return `As ${s.project} · ${s.confidence}`;
  return `${s.setting[0].toUpperCase()}${s.setting.slice(1)} · ${s.confidence}`;
}
