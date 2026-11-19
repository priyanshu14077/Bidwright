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
  options: string[];
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
  proposal_id?: number | null;
  proposal_reference?: string | null;
  proposal_version?: number | null;
  proposal_status?: VersionStatus | null;
  proposal_total?: number | null;
  proposal_currency?: string | null;
  gate1_decision?: "qualify" | "ask" | "decline" | null;
  gate2_decision?: "approve" | "override" | "reject" | null;
  open_questions?: number | null;
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

export const SIGNED_OUT = "bidwright:signed-out";

/** An error from the API. detail keeps the server's structured answer (approval problems, revision conflicts). */
export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail
      : (detail as { message?: string })?.message ?? JSON.stringify(detail));
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (res.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event(SIGNED_OUT));
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* body was not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

const json = (body: unknown): RequestInit =>
  ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const send = (method: string, body?: unknown): RequestInit =>
  ({ method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });

export type Role = "owner" | "admin" | "estimator" | "viewer";
export type Permission = "read" | "intake" | "review" | "approve" | "reference" | "backtest" | "members" | "workspace";
export type Workspace = { org_id: string; slug: string; name: string; is_demo: boolean; role: Role };
export type Me = { user: { user_id: string; email: string; name: string }; workspace: Workspace | null; role: Role | null;
  permissions: Permission[]; workspaces: Workspace[] };
export type Members = {
  members: { user_id: string; email: string; name: string; role: Role; joined_at: string; last_login_at: string | null }[];
  invitations: { email: string; role: Role; created_at: string; expires_at: string }[];
};

export const auth = {
  me: () => call<Me>("/api/auth/me"),
  login: (email: string, password: string) => call("/api/auth/login", json({ email, password })),
  signup: (body: { name: string; email: string; password: string; workspace: string; practice_description?: string;
    invitation?: string }) => call<{ org_id: string }>("/api/auth/signup", json(body)),
  logout: () => call("/api/auth/logout", { method: "POST" }),
  switchTo: (org_id: string) => call("/api/auth/switch", json({ org_id })),
  createWorkspace: (name: string, practice_description?: string) =>
    call<{ org_id: string }>("/api/auth/workspaces", json({ name, practice_description })),
  join: (token: string) => call<{ org_id: string }>("/api/auth/join", json({ token })),
  members: () => call<Members>("/api/auth/members"),
  invite: (email: string, role: Role) => call<{ link: string }>("/api/auth/invitations", json({ email, role })),
  changeRole: (userId: string, role: Role) => call(`/api/auth/members/${userId}`, send("PUT", { role })),
  remove: (userId: string) => call(`/api/auth/members/${userId}`, send("DELETE")),
};

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
  intakeEvaluation: () => call<IntakeEvaluation | null>("/api/evaluation/intake"),
  pdfUrl: (documentId: number) => `/api/documents/${documentId}/pdf`,
  fees: (id: number, c: PricingChoices) => {
    const q = new URLSearchParams(Object.entries(c).filter(([, v]) => v !== undefined && v !== "" && v !== null)
      .map(([k, v]) => [k, String(v)]));
    return call<FeeEstimate>(`/api/envelopes/${id}/fees?${q}`);
  },
};

const v = (p: number, n: number) => `/api/proposals/${p}/versions/${n}`;

export const proposals = {
  list: (envelopeId?: number) => call<ProposalRow[]>(`/api/proposals${envelopeId ? `?envelope_id=${envelopeId}` : ""}`),
  get: (p: number) => call<Proposal>(`/api/proposals/${p}`),
  version: (p: number, n: number) => call<Version>(v(p, n)),
  create: (envelopeId: number, body: PricingChoices & { title?: string }) =>
    call<Version>(`/api/envelopes/${envelopeId}/proposals`, json(body)),
  editSection: (p: number, n: number, code: string, body: { revision: number; body?: string; reason?: string }) =>
    call<Version>(`${v(p, n)}/sections/${code}`, send("PATCH", body)),
  editLine: (p: number, n: number, line: number, body: { revision: number; amount_local: number; note?: string; reason?: string }) =>
    call<Version>(`${v(p, n)}/lines/${line}`, send("PATCH", body)),
  addLine: (p: number, n: number, body: { revision: number; service: string; stage: string; amount_local: number;
    note: string; reason?: string }) => call<Version>(`${v(p, n)}/lines`, json(body)),
  deleteLine: (p: number, n: number, line: number, revision: number, reason: string) =>
    call<Version>(`${v(p, n)}/lines/${line}?${new URLSearchParams({ revision: String(revision), reason })}`, send("DELETE")),
  reprice: (p: number, n: number, body: PricingChoices & { revision: number; reason?: string }) =>
    call<Version>(`${v(p, n)}/reprice`, json(body)),
  approve: (p: number, n: number, revision: number, reason?: string) =>
    call<Version>(`${v(p, n)}/approve`, json({ revision, reason })),
  newVersion: (p: number, reason?: string) => call<Version>(`/api/proposals/${p}/versions`, json({ reason })),
  exportFiles: (p: number, n: number) => call<{ outputs: Output[]; reused: boolean }>(`${v(p, n)}/export`, { method: "POST" }),
  downloadUrl: (outputId: number) => `/api/outputs/${outputId}`,
  draft: (p: number, n: number, body: { revision: number; sections?: string[]; reason?: string }) =>
    call<Version & { draft: DraftReport }>(`${v(p, n)}/draft`, json(body)),
};

export const settings = {
  pricing: () => call<PricingSettings>("/api/settings/pricing"),
  setCost: (body: { country: string; city: string; cost_type: string; usd_m2: number; price_year?: number;
    note?: string; reason?: string }) => call("/api/settings/pricing/construction-costs", send("PUT", body)),
  setMarket: (country: string, body: { band?: string; fee_factor?: number; escalation_pct?: number; reason?: string }) =>
    call(`/api/settings/pricing/markets/${country}`, send("PUT", body)),
  setFeeMethod: (key: string, value: number | string, reason?: string) =>
    call(`/api/settings/pricing/fee-method/${encodeURIComponent(key)}`, send("PUT", { value, reason })),
  library: () => call<Clause[]>("/api/settings/library"),
  setClause: (kind: string, key: string, body: { body: string; label?: string; reason?: string }) =>
    call(`/api/settings/library/${kind}/${encodeURIComponent(key)}`, send("PUT", body)),
  deleteClause: (kind: string, key: string, reason: string) =>
    call(`/api/settings/library/${kind}/${encodeURIComponent(key)}?${new URLSearchParams({ reason })}`, send("DELETE")),
  setStudio: (code: string, body: { name: string; city: string; country: string; reason?: string }) =>
    call(`/api/settings/studios/${code}`, send("PUT", body)),
};

// ---------------------------------------------------------------- pricing and proposals

export type PricingChoices = { complexity?: string; cost_type?: string; construction_cost_usd_m2?: number | null };
export type Factor = { name: string; value: number | string; source: string };
export type EngineLine = { service: string; stage: string; basis: string; basis_qty: number; basis_unit: string;
  amount_usd: number; amount_local: number; factors: Factor[] };
export type Reimbursable = { type: string; qty: number; unit_rate_local: number; amount_local: number; note: string };
export type Milestone = { seq: number; milestone: string; stage: string | null; pct: number; amount_local: number };
export type FeeRange = { low_local: number; high_local: number; basis: string; cases: string[] };
export type FeeEstimate = {
  envelope_id?: number; record_version?: number; engine_version: string; currency: string; fx_per_usd: number;
  inputs: { services: string[]; stages: string[]; country: string; city: string | null; cost_type: string | null;
    complexity: string; client_status: string; year: number; assumed: string[] };
  lines: EngineLine[]; fee_total_local: number; fee_total_usd: number; reimbursables: Reimbursable[];
  reimbursables_total_local: number; payment_schedule: Milestone[]; programme: { stage: string; label: string; duration: string }[];
  deliverables: { item: string; qty: number }[]; factors: Factor[]; range: FeeRange | null; warnings: string[];
};

export type VersionStatus = "draft" | "approved" | "superseded";
export type SectionOrigin = "record" | "engine" | "library" | "claude" | "estimator" | "pending";
export type CitedSource = { id: string; kind: "record" | "rfp" | "past_proposal"; quote: string | null;
  document?: string | null; document_id?: number | null; page?: number | null; rects?: Rect[] | null };
export type Citation = { part: string; sources: CitedSource[] };
type SectionBase = { seq: number; title: string; body: string | null; origin: SectionOrigin; citations: Citation[];
  updated_by: string | null; updated_at: string };
export type Cover = { client: string | null; project: string | null; location: string; reference: string;
  client_reference: string | null; date: string; validity_days: number; currency: string };
export type ProseCode = "understanding" | "scope" | "exclusions" | "terms";
/** One variant per section: the data a section holds depends on what it is. */
export type Section = SectionBase & (
  | { code: "cover"; kind: "cover"; data: Cover }
  | { code: ProseCode; kind: "prose"; data: unknown }  // drafting inputs or drafted structure; the body is what shows
  | { code: "deliverables"; kind: "table"; data: { item: string; qty: number }[] }
  | { code: "programme"; kind: "table"; data: { stage: string; label: string; duration: string }[] }
  | { code: "reimbursables"; kind: "table"; data: Reimbursable[] }
  | { code: "payment"; kind: "table"; data: Milestone[] }
  | { code: "fees"; kind: "table"; data: null });
export type FeeLine = { line_no: number; service: string; stage: string; basis: string | null; basis_qty: number | null;
  basis_unit: string | null; engine_amount_local: number | null; amount_local: number; factors: Factor[]; note: string | null };
export type ProposalEvent = { event_id: number; version_id: number | null; action: string; target: string | null;
  old: unknown; new: unknown; reason: string | null; actor: string; at: string };
export type DraftReport = { sections: string[]; notes: string[]; warnings: string[]; attempts: number; trace_id: string;
  llm_call: { call_id: number; model: string; cost_usd: number | null; input_tokens: number; output_tokens: number } | null };
export type Version = {
  version_id: number; proposal_id: number; version_no: number; status: VersionStatus; based_on: number | null;
  record_version: number; pricing_choices: PricingChoices; estimate: FeeEstimate; engine_version: string;
  currency: string; fx_per_usd: number; engine_total_local: number; fee_total_local: number; revision: number;
  created_by: string; created_at: string; approved_by: string | null; approved_at: string | null;
  reference: string; title: string; envelope_id: number; stale: boolean; reimbursables_total_local: number;
  fee_lines: FeeLine[]; sections: Section[]; events: ProposalEvent[]; outputs: Output[];
};
export type Output = { output_id: number; format: "docx" | "pdf"; file_name: string; size_bytes: number; template: string;
  created_by: string; created_at: string };
export type ProposalRow = { proposal_id: number; reference: string; title: string; envelope_id: number; created_at: string;
  created_by: string; latest_version: number; status: VersionStatus; currency: string; fee_total_local: number };
export type Proposal = { proposal_id: number; reference: string; title: string; envelope_id: number; created_by: string;
  created_at: string; versions: { version_no: number; status: VersionStatus; record_version: number; currency: string;
    engine_total_local: number; fee_total_local: number; revision: number; created_by: string; created_at: string;
    approved_by: string | null; approved_at: string | null }[]; events: ProposalEvent[] };

// ---------------------------------------------------------------- settings

export type Readiness = { area: string; severity: "blocks" | "limits"; message: string };
export type CostRow = { country: string; country_name: string; city: string; cost_type: string; usd_m2: number;
  price_year: number; basis: "benchmark" | "derived" | "proxy" | "synthetic" | "estimator"; source: string | null;
  note: string | null; updated_at: string; updated_by: string | null };
export type MarketRow = { country: string; country_name: string; national_currency: string; band: "high" | "low";
  fee_factor: number; fee_factor_source: string | null; escalation_pct: number; escalation_source: string | null };
export type SettingChange = { change_id: number; setting: string; key: string; old: unknown; new: unknown;
  reason: string | null; changed_by: string; changed_at: string };
export type StudioRow = { code: string; name: string; city: string; country: string };
export type PricingSettings = {
  readiness: Readiness[];
  fee_method: { key: string; value_text: string; value: number | null; unit: string | null; rationale: string | null }[];
  cost_ratios: { cost_type: string; ratio: number }[];
  markets: MarketRow[]; construction_costs: CostRow[]; studios: StudioRow[]; changes: SettingChange[];
};
export type Clause = { kind: "scope" | "exclusion" | "terms" | "position"; key: string; service: string | null;
  stage: string | null; seq: number; label: string | null; body: string; updated_at: string; updated_by: string | null };

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


// ---------------------------------------------------------------- guided flow: gate 1 and the fee card

export type EvidenceKind = "text" | "reply" | "comparable" | "rule" | "setting" | "engine";
export type Evidence = {
  id: number | null; kind: EvidenceKind; why: string; subject?: string; quote?: string | null;
  document_id?: number | null; field_value_id?: number | null; file_name?: string | null; channel?: string | null;
  received_at?: string | null;
  has_text?: boolean; has_pdf?: boolean; page?: number | null; span?: number[] | null;
  ref?: { comparables?: string[]; low?: number; high?: number; confidence?: string } | null;
};
export type Confidence = "low" | "medium" | "high";
export type Estimate = { value: unknown; low?: number; high?: number; confidence: Confidence; comparables: string[];
  method: string; kind?: string; referenced?: boolean };
export type FieldStatus = "found" | "ambiguous" | "estimated" | "missing";
export type GateField = { name: string; label: string; status: FieldStatus; value: unknown; display: string;
  range_display?: string | null; weight: number; pricing_critical: boolean; evidence: Evidence[];
  estimate: Estimate | null; note: string | null };
export type GateRule = { name: string; outcome: "no-go" | "clarify" | "flag"; message: string; why: string;
  evidence: Evidence[] };
export type Recommendation = "go" | "clarify" | "no-go";
export type Gate1 = {
  envelope_id: number; computed_at: string; recommendation: Recommendation;
  completeness: { score: number; found: number; total: number };
  fields: GateField[]; rules: GateRule[]; flags: GateRule[];
  questions: { items: { field: string; question: string; worded: boolean }[]; message: string; worded: boolean };
  reference_project: { reference: string; title: string; via: string; said: string } | null;
  routed_studio: string | null;
  decision: { decision: "qualify" | "ask" | "decline"; recommendation: string; reason: string | null; note: string | null;
    decided_by: string; decided_at: string } | null;
  decline_reasons: string[];
};
export type Assumption = { field: string; label: string; status: FieldStatus; display: string;
  range_display: string | null; estimate: Estimate | null; note: string | null; evidence: Evidence[];
  swing?: { down: number; up: number } };
export type FeeDecision = { decision: "approve" | "override" | "reject"; amount: number | null;
  engine_amount: number | null; currency: string | null; reason: string | null; note: string | null;
  decided_by: string; decided_at: string; proposal_id: number | null };
export type FeeCard = {
  envelope_id: number; recommendation: Recommendation; assumptions: Assumption[]; rules: GateRule[]; flags: GateRule[];
  decision: FeeDecision | null;
  proposal: { proposal_id: number; reference: string; version_no: number; status: VersionStatus;
    fee_total_local: number; outputs: Output[] } | null;
} & (
  | { status: "missing"; missing: { field: string; label: string }[] }
  | { status: "error"; message: string }
  | { status: "priced"; currency: string; opening: number; opening_usd: number;
      won: { low: number; high: number; basis: string; cases: string[] } | null;
      band: { low: number; high: number }; swings: { field: string; label: string; down: number; up: number }[];
      stage_options: { stage: string; label: string; adds: number }[];
      stages: { stage: string; label: string; amount: number }[]; scope: string; stage_line: string;
      lines: { service: string; stage: string; amount: number; basis_qty: number; basis_unit: string;
        factors: { name: string; value: number | string; source: string }[] }[];
      why: { label: string; value: number | string; source: string }[]; warnings: string[];
      checks: { level: "check" | "info"; text: string; evidence: Evidence[] }[];
      comparables: { currency: string; items: Comparable[] }; title: string | null }
);
export type Channel = "whatsapp" | "email" | "notes" | "upload";

export type IntakeEvaluation = {
  meta: { run_at: string; dataset: string; model: string; prompt_version: string; engine_version: string };
  summary: { requests: number; recommendation_accuracy: number; status_accuracy: number; value_accuracy: number;
    question_coverage: number; false_gaps: string; gaps_caught: string; cost_usd: number; llm_calls: number;
    recommendation_confusion: Record<string, Record<string, number>>; status_accuracy_per_field: Record<string, number> };
  results: { case_id: string; kind: string; channel: string; recommendation: { ok: boolean; got: string; expected: string };
    fields_ok: number; fields: number; missed: string[] }[];
};

import type { TraceSpan } from "@/components/ui/agent-trace";

// ---------------------------------------------------------------- the pricing logic register

export type LogicEntry = {
  id: string; section: string; title: string; value: string; rule: string; source: string;
  kind: "cost" | "setting" | "market" | "rule" | "policy" | "built_in";
  changed: LogicChange | null; history: LogicChange[]; edit: string | null; editable: boolean;
};
export type LogicChange = { by: string; at: string; reason: string | null; old: Record<string, unknown> | null;
  new: Record<string, unknown> | null };
export type LogicRegister = {
  engine_version: string; questions_prompt: string;
  sections: { key: string; title: string; about: string }[]; entries: LogicEntry[];
  request: { envelope_id: number; title: string | null; priced: boolean; currency?: string; opening?: number; used: string[];
             effects?: Record<string, string[]> } | null;
};

export const guided = {
  logic: (envelopeId?: number) => call<LogicRegister>(`/api/logic${envelopeId ? `?envelope_id=${envelopeId}` : ""}`),
  paste: (body: { text: string; channel: Channel; sender?: string }) =>
    call<{ envelope_id: number; created: boolean }>("/api/envelopes/text", json(body)),
  upload: (files: File[], channel: Channel) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    form.append("channel", channel);
    return call<{ envelope_id: number; created: boolean }>("/api/envelopes", { method: "POST", body: form });
  },
  reply: (id: number, body: { text: string; channel: Channel }) => call(`/api/envelopes/${id}/text`, json(body)),
  gate1: (id: number) => call<Gate1>(`/api/envelopes/${id}/gate1`),
  decide: (id: number, body: { decision: "qualify" | "ask" | "decline"; reason?: string; note?: string }) =>
    call<Gate1>(`/api/envelopes/${id}/gate1/decision`, json(body)),
  reword: (id: number) => call<Gate1>(`/api/envelopes/${id}/gate1/questions`, { method: "POST" }),
  fee: (id: number) => call<FeeCard>(`/api/envelopes/${id}/fee`),
  decideFee: (id: number, body: { decision: "approve" | "override" | "reject"; amount?: number; reason?: string }) =>
    call<FeeCard>(`/api/envelopes/${id}/fee/decision`, json(body)),
  proposal: (id: number) => call<ProposalStep>(`/api/envelopes/${id}/proposal`),
  draftProposal: (id: number) => call<ProposalStep>(`/api/envelopes/${id}/proposal/draft`, { method: "POST" }),
  exportProposal: (id: number, template: TemplateKey) =>
    call<ProposalStep>(`/api/envelopes/${id}/proposal/export`, json({ template })),
  documentText: async (documentId: number) => {
    const res = await fetch(`/api/documents/${documentId}/text`);
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.text();
  },
  resolve: (id: number, field: string, fieldValueId: number, reason: string) =>
    call(`/api/envelopes/${id}/fields/${field}`, json({ action: "resolve_conflict", field_value_id: fieldValueId, reason })),
};

export type TemplateKey = "full" | "letter";
export type ProposalStep = {
  proposal: (Version & { outputs_by_template: Record<TemplateKey, Output[]>; pending: string[] }) | null;
  templates: { key: TemplateKey; name: string }[];
};

// ---------------------------------------------------------------- chat

export type ChatAction = { action_id: number; kind: string; envelope_id: number; summary: string;
  status: "pending" | "confirmed" | "cancelled" | "failed"; before?: string; after?: string; reason?: string };
export type RequestCard = { type: "request"; envelope_id: number; title: string | null; recommendation: Recommendation;
  headline: string; why: string | null; counts: Record<string, number>;
  questions: { field: string; question: string }[]; message: string };
export type ChatMessage = { id: number; role: "user" | "assistant" | "system"; created_at: string; text: string;
  sources?: Record<string, Evidence>; cards?: RequestCard[]; actions?: ChatAction[];
  downloads?: { label: string; url: string }[]; status?: string };
export type ChatThread = { thread_id: number; envelope_id: number | null; messages: ChatMessage[] };

export const chat = {
  send: (body: { message: string; thread_id?: number; envelope_id?: number }) => call<ChatThread>("/api/chat", json(body)),
  thread: (id: number) => call<ChatThread>(`/api/chat/${id}`),
  forRequest: (envelopeId: number) => call<ChatThread | null>(`/api/envelopes/${envelopeId}/chat`),
  decide: (actionId: number, choice: "confirm" | "cancel") =>
    call<ChatThread>(`/api/chat/actions/${actionId}/${choice}`, { method: "POST" }),
};
