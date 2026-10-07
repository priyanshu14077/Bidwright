import type { Reference } from "./api";

let labels: Record<string, string> = {};

export function setReference(ref: Reference) {
  labels = Object.fromEntries([...ref.typology, ...ref.service, ...ref.stage].map((r) => [r.code, r.label]));
}

export const codeLabel = (code: string) => labels[code] ?? code.replace(/_/g, " ");

const COUNTRY: Record<string, string> = {
  AE: "UAE", SA: "Saudi Arabia", QA: "Qatar", KW: "Kuwait", BH: "Bahrain", OM: "Oman", EG: "Egypt", JO: "Jordan",
  IN: "India", SG: "Singapore", VN: "Vietnam", CN: "China", AU: "Australia", ES: "Spain",
};
export const countryName = (code: string | null | undefined) => (code ? COUNTRY[code] ?? code : "");

export function formatValue(kind: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.map((v) => formatValue(kind, v)).join(", ");
  if (kind === "area") return `${Math.round(Number(value)).toLocaleString("en-GB")} m²`;
  if (kind === "vocab" || kind === "vocab_list") return codeLabel(String(value));
  if (kind === "country") return countryName(String(value));
  if (kind === "datetime") return formatDateTime(String(value));
  if (kind === "date") return formatDate(String(value));
  if (kind === "integer") return Number(value).toLocaleString("en-GB");
  return String(value);
}

export function formatDate(iso: string | null | undefined) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function formatDateTime(iso: string) {
  // keep the deadline in the time zone it was written in
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2})?([+-]\d{2}:\d{2}|Z)?$/);
  if (!m) return iso;
  const day = new Date(`${m[1]}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  return `${day}, ${m[2]}${m[3] ? ` (UTC${m[3] === "Z" ? "" : m[3]})` : ""}`;
}

export function daysLeft(deadline: string | null, from: string) {
  if (!deadline) return null;
  return Math.floor((new Date(deadline).getTime() - new Date(from).getTime()) / 86_400_000);
}

export const money = (n: number | null | undefined, currency: string) =>
  n == null ? "" : `${currency} ${Math.round(n).toLocaleString("en-GB")}`;

export const LEAD: Record<string, string> = {
  qualified: "Qualified",
  needs_info: "Needs information",
  recommend_no_bid: "Recommend no-bid",
};

export const ORIGIN: Record<string, string> = { ai: "Extracted", engine: "Engine", human: "Reviewer", generator: "Generator" };

export const DOC_CLASS: Record<string, string> = {
  main_rfp: "Main RFP", design_brief: "Design brief", area_schedule: "Area schedule", client_terms: "Client terms",
  email: "Email", drawings: "Drawings", other: "Other", unsupported: "Not read",
};
