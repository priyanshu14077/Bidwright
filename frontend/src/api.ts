export type Rect = [number, number, number, number];

export type Source = {
  field_value_id: number;
  value_text: string | null;
  normalized: unknown;
  notes: string[];
  evidence: Record<string, unknown> | null;
  reason: string | null;
  document_id: number | null;
  file_name: string | null;
  doc_class: string | null;
  page: number | null;
  quote: string | null;
  bbox: Rect[] | null;
  quote_verbatim: boolean | null;
  confidence: number | null;
  origin: "ai" | "engine" | "human" | "generator";
  kind: "fact" | "suggestion";
  status: "proposed" | "confirmed" | "rejected" | "superseded";
};

export type Flag = { field: string | null; rule: string; severity: "info" | "warn" | "block"; message: string;
  evidence: Record<string, unknown> | null; field_value_id: number | null };

export type Conflict = { conflict_id: number; field: string; field_value_ids: number[]; summary: string };

export type RecordField = {
  label: string;
  group: string;
  kind: string;
  is_list: boolean;
  value: unknown;
  suggestion: unknown;
  suggestion_evidence: Record<string, unknown> | null;
  sources: Source[];
  conflict: Conflict | null;
  flags: Flag[];
  edited: boolean;
};

export type Envelope = {
  envelope_id: number;
  title: string | null;
  project_name?: string | null;
  channel: string;
  sender: string | null;
  received_at: string;
  submission_deadline: string | null;
  status: "received" | "reading" | "extracting" | "review" | "confirmed" | "failed";
  lead_status: "qualified" | "needs_info" | "recommend_no_bid" | null;
  completeness: number | null;
  country: string | null;
  city: string | null;
  tier: string | null;
  billing_currency: string | null;
  routed_studio: string | null;
  open_conflicts?: number;
  flags?: number;
  documents?: number;
  confirmed_version?: number | null;
  error?: string | null;
};

export type Doc = { document_id: number; file_name: string; doc_class: string | null; page_count: number | null;
  supported: boolean; has_pdf: boolean; parent_document_id: number | null;
  pages: { page: number; width: number; height: number }[] };

export type Comparable = { reference: string; title: string; city: string; country: string; typology: string;
  services: string[]; size_m2: number; size_basis: string; fee_local: number; currency: string;
  fee_in_target: number | null; fee_per_m2_in_target: number | null; status: string; loss_reason: string | null;
  year: number | null };

export type Detail = {
  envelope: Envelope;
  documents: Doc[];
  record: Record<string, RecordField> & { _flags: Flag[] };
  comparables: { currency: string; fx_as_of: string; fx_note: string; label: string; items: Comparable[] } | null;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* body was not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

const json = (body: unknown): RequestInit =>
  ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  inbox: () => call<Envelope[]>("/api/envelopes"),
  detail: (id: number) => call<Detail>(`/api/envelopes/${id}`),
  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return call<{ envelope_id: number; created: boolean }>("/api/envelopes", { method: "POST", body: form });
  },
  rerun: (id: number) => call(`/api/envelopes/${id}/extract`, { method: "POST" }),
  change: (id: number, field: string, body: { action: string; reason: string; value?: unknown; field_value_id?: number }) =>
    call(`/api/envelopes/${id}/fields/${field}`, json(body)),
  confirm: (id: number, reason: string) => call<{ version: number }>(`/api/envelopes/${id}/confirm`, json({ reason })),
  audit: (id: number) => call<Audit>(`/api/envelopes/${id}/audit`),
  mapping: (id: number) => call<Mapping>(`/api/envelopes/${id}/mapping`),
  reference: () => call<Reference>("/api/reference"),
  addSynonym: (body: { synonym: string; target_table: string; target_code: string; scope: string }) =>
    call("/api/reference/synonyms", json(body)),
  observability: () => call<Observability>("/api/observability"),
  evaluation: () => call<Evaluation | null>("/api/evaluation/latest"),
  pdfUrl: (documentId: number) => `/api/documents/${documentId}/pdf`,
};

export type Audit = {
  values: { field_value_id: number; field: string; value_text: string | null; normalized: unknown; origin: string;
    kind: string; status: string; confidence: number | null; created_at: string; file_name: string | null;
    page: number | null; model: string | null; prompt_version: string | null; engine_version: string | null }[];
  changes: { change_id: number; field: string; old_value: unknown; new_value: unknown; action: string; reason: string;
    changed_by: string; changed_at: string }[];
  runs: { run_id: number; kind: string; model: string | null; prompt_version: string | null; engine_version: string | null;
    status: string; trace_id: string; started_at: string; finished_at: string | null; file_name: string | null;
    calls: number; cost_usd: number | null }[];
  versions: { version: number; confirmed_by: string; confirmed_at: string }[];
};

export type Mapping = {
  sections: { code: string; title: string; in_archive: string; items: { field: string; label: string; value: unknown }[] }[];
  unmapped: { text: string; kind: string; file_name?: string; page?: number }[];
};

export type Reference = {
  typology: { code: string; label: string }[];
  service: { code: string; label: string }[];
  stage: { code: string; label: string; seq: number }[];
  synonyms: { synonym: string; target_table: string; target_code: string; scope: string }[];
  locations: { country: string; country_name: string; city: string; tier: string; national_currency: string }[];
  rules: { name: string; effect: string; message: string; expression: Record<string, unknown> }[];
  criticality: { field: string; pricing_critical: boolean; weight: number }[];
  archive: { proposal_id: string; reference: string; title: string; typology: string; city: string; country: string;
    currency: string; fee_total_local: number; status: string; dataset_version: string; data_origin: string }[];
};

export type Observability = {
  totals: { calls: number; traces: number; cost_usd: number | null; input_tokens: number | null; output_tokens: number | null;
    cache_read_tokens: number | null; p50_ms: number | null; p95_ms: number | null; rejected: number; errors: number };
  by_version: { model: string; prompt_version: string; calls: number; cost_usd: number | null; avg_ms: number; rejected: number }[];
  recent: { call_id: number; trace_id: string; attempt: number; model: string; prompt_version: string;
    input_tokens: number | null; output_tokens: number | null; cache_read_tokens: number | null; latency_ms: number | null;
    cost_usd: number | null; stop_reason: string | null; validation_error: string | null; error: string | null;
    created_at: string; file_name: string | null; envelope_id: number | null }[];
};

export type Evaluation = {
  meta: { run_at: string; dataset: string; model: string; prompt_version: string; engine_version: string };
  summary: { pricing_critical_accuracy: number; all_fields_accuracy: number; per_field: Record<string, number>;
    conflict_recall: string; false_conflicts: number; citation_coverage: number; quote_verbatim_rate: number;
    cost_usd: number; llm_calls: number; rejected_attempts: number };
  results: { proposal_id: string; envelope_id: number; style: string; traps: string[];
    fields: Record<string, { truth: unknown; got: unknown; ok: boolean }>;
    conflicts: { planted: string[]; found: string[]; detected: string[]; false: string[] }; lead_status: string }[];
};
