# SOG RFP Platform: Phase 1 proof of concept

Status: revision 3, 2026-10-05. Day 1 built; see "Build status" at the end.
Previous: revision 2, 2026-10-04. Revised after analysing the synthetic archive (`data/archive/v0-synthetic/`, unpacked from `docs/files.zip`).
Source: `SOG_RFP_Platform_Phase1_Implementation_Plan.docx`.

## Goal

Within two days, show SOG one RFP pack going end to end: intake, extraction with page citations, flagged conflicts, engine enrichment against their archive, then human review and confirmation. Back it with an accuracy number measured on a gold set. Everything except the stubbed edges carries into Phase 1 proper and Phase 2.

The demo should prove three claims from the Phase 1 plan:

1. At least 90% accuracy on pricing-critical fields (typology, areas, services, stage package, location).
2. Every extracted value is linked to a highlighted source on a page.
3. Conflicts between documents are flagged, not silently resolved.

## What the synthetic archive contains

Verified on 2026-10-04:

- **14 proposals** (P01–P14), not 15. Five studios: DXB, BCN, SIN, SGN (Ho Chi Minh City), CAN (Guangzhou).
- **Project markets:** UAE, KSA, Spain, Singapore, Vietnam, China, India. No Australia.
- **Seven currencies:** AED, SAR, EUR, SGD, USD, CNY, INR.
- **Every docx matches the JSON:** areas (including P13's sq ft and hectare sites), fee lines, totals, currency, units/keys and stage labels.
- **Arithmetic holds:** fee lines sum to the total, payment schedules sum to 100% and to the total, USD conversions are correct.
- **The SQL loads:** `sog_archive_schema.sql` + `sog_synthetic_seed.sql` run cleanly on `pgvector/pgvector:pg16`. 14 proposals, 88 fee lines, 33 service rows, 12 locations, 7 FX rates.
- **Pricing data is included:** fee bases (% of construction cost, % of fit-out, % of landscape, per hectare), construction-cost benchmarks with URLs, assumptions, and a per-proposal `pricing_trace`.

### Facts that change the plan

1. **These are proposals, not RFPs.** The RFP extraction pipeline has no input to run on and no gold pairs to score against. We have to generate RFP packs.
2. **All 14 docs use one template** with fixed headings and tables. Extracting from them proves little; real RFPs come from other organisations in many formats. Archive extraction is still a valid second test.
3. **The documents are .docx, not PDF.** Source highlighting needs page coordinates; docx has no pages. LibreOffice is not installed on this machine.
4. **The billing currency is not always the national currency.** The Vietnam projects (P09, P10) are priced in USD. "Local currency" has to be a market rule learned from history, not a country → currency lookup.
5. **The bidding studio is not always in the project's country.** SOG Dubai bid Riyadh (P03) and Mumbai (P13). Routing an RFP to a studio is an enrichment step.

### Defects to fix in the provided schema and data

| # | Defect | Fix |
|---|---|---|
| 1 | JSON `services` uses `interior`; `ref.service` and `fee_lines` use `interior_design` | Loader maps to `interior_design`; fix the JSON at source |
| 2 | Stage labels "Feasibility Study", "Construction Stage Design Review" and "Basic Design / Design Development" have no synonym and don't match a `ref.stage` label | Add synonyms; split compound labels in the normaliser |
| 3 | `ref.term_synonym` key is `synonym` only, so one word can map to only one code. "Basic design" and "preliminary design" mean different stages in different countries | Key on `(synonym, target_table, scope)`, where scope is a country or `global` |
| 4 | `lineage.field_value` key is `(proposal_id, field)`: one value per field | Surrogate id, plus `run_id`, so conflicting candidates and re-runs can coexist |
| 5 | `field_value.value` is text | JSONB raw value + JSONB normalised value; add `quote` |
| 6 | `REVOKE UPDATE, DELETE ... FROM PUBLIC` doesn't stop the table owner (the app role) | Trigger that raises on UPDATE/DELETE |
| 7 | `field_change` has no foreign key to the value it changes | Reference `field_value.id` |
| 8 | `ref.qualification_rule`, `ref.field_criticality`, `archive.proposal_section` and all `lineage` tables are empty | Seed PoC rules and criticality; derive sections from the docx headings |
| 9 | `embedding vector(1536)` assumes an OpenAI-size embedding; Anthropic has no embeddings API | Drop from the PoC; pick a provider (e.g. Voyage) when retrieval is needed |
| 10 | FX rates share one `as_of` date; EUR/SGD/CNY/INR are illustrative | Acceptable for the demo; label it on screen |
| 11 | `ref.location` has 12 cities only; any other city gets no tier | Return "unknown tier" as a flag, not a guess |

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Database | Self-hosted Postgres 16 + pgvector in Docker Compose | Shared by the Python service and n8n; carries over to Azure Postgres |
| Schema baseline | The provided `ref` / `archive` / `lineage` schemas, with fixes 1–9, plus a new `intake` schema | Built from SOG's proposal shape; no parallel model |
| Migrations | Alembic, owned by the Python service; migration 0001 written by hand from `sog_archive_schema.sql` | One owner of the data shape |
| Alembic scope | `include_name` limited to `ref`, `archive`, `lineage`, `intake` | Autogenerate can't touch n8n or anything else |
| n8n isolation | Same Postgres server, separate `n8n` database and role; calls the Python API over HTTP | Python validates every write |
| Seed loading | Python loader reads `sog_synthetic_archive.json` by dataset version and upserts (safe to run twice) | Swapping `v0-synthetic` for `v1-real` is a flag, not a migration. The seed SQL stays as reference |
| LLM | Anthropic API direct, model per task in config, behind one client interface | Allowed for the PoC |
| LLM observability | Every call logged to `intake.llm_calls` with a trace id | Stated requirement |
| Runs on | Local machines | Azure comes in Phase 1 proper |

The Alembic rules from revision 1 still apply. Review every migration by hand; write constraints, triggers and enums explicitly.

## Data model

Kept from the provided schema (with the fixes above):

- `ref`: typology, service, stage, term_synonym, studio, location, fx_rate, escalation_index, benchmark_source, qualification_rule, field_criticality.
- `archive`: client, proposal, proposal_service, proposal_stage, fee_line, reimbursable, payment_milestone, deliverable, tc_deviation, proposal_section, pricing_trace (synthetic only).
- `lineage`: source_document, field_value, field_change.

New `intake` schema for incoming RFPs:

- `envelopes`: channel, sender, received_at, detected country/city, location id, billing currency, routed studio, lead status, completeness, owner.
- `documents`: one per file. `sha256` unique, filename, mime type, storage URI, document class, language, page count, supported flag.
- `pages`: page number, size, layout lines (text + bounding box) as JSONB.
- `extraction_runs`: pass (1 or 2), model, prompt version, schema version, engine version, dataset version, status, trace id.
- `llm_calls`: run, attempt, model, prompt version, request hash, tokens in/out, latency, cost, raw response, validation error.
- `field_conflicts`: field plus the competing `lineage.field_value` ids.
- `engine_flags`: field value, rule, severity, message, evidence.
- `opportunities`: the confirmed record as a JSONB snapshot, versioned. On confirm, it is also written to `archive.proposal` with `data_origin = 'rfp_new'` (request side only), so the archive grows with every confirmed RFP.

`lineage.field_value` and `lineage.field_change` serve both the archive and new RFPs. Each row references either a proposal or an envelope.

## Schema v0

The field list comes from `archive.proposal` (request side) plus what an RFP contains that a proposal doesn't:

- **From the archive:** client, client type, client status, country, city, location tier, typology, sub-typology, GFA, site area, landscape area, fit-out area, units, keys, complexity and notes, services, stage package, deliverables, T&C deviations requested.
- **RFP-only:** RFP reference, issuer, issue date, clarification deadline, site visit, submission deadline, requested fee format, requested currency, payment terms, liability/insurance requirements, evaluation criteria, special requests.

Pricing-critical fields (seeded into `ref.field_criticality`): typology, GFA, site area, fit-out area, landscape area, services, stage package, country, city. These are the accuracy target fields.

## Gold set

Two tests, reported separately:

1. **RFP extraction (the main number).** Generate one RFP pack per proposal from the JSON request side (14 packs). Each pack contains:
   - an email body (.eml) holding the submission deadline;
   - the main RFP as a PDF, in varied layouts that don't copy SOG's template;
   - an area schedule as a table PDF or xlsx;
   - client terms as a PDF, carrying the liability/T&C requests from `tc_deviations`.

   Planted traps: sq ft (India, P13), hectares, regional stage names (from `document_variations` + new ones), and a deliberate brief vs area schedule GFA conflict in about 4 packs. Ground truth is the JSON plus a list of planted conflicts. PDFs are generated directly (reportlab), so page coordinates are exact.
2. **Archive extraction (secondary).** Extract the 14 docx proposals into the archive shape and compare with the JSON. Citations go to paragraph or table cell; the PoC has no page coordinates for docx, unless we install LibreOffice to convert to PDF.

Caveat for the client: synthetic accuracy is an upper bound. The real number comes from 20–30 real RFP and proposal pairs.

## Engine v0

- **Taxonomy lock:** values outside `ref.typology` / `ref.service` / `ref.stage` are rejected or mapped through `term_synonym` (scoped by country).
- **Units:** sq ft → m², ha → m², Indian number formats, dates.
- **Origin:** country/city → `ref.location` tier. Billing currency = most common currency in the archive for that country, else the national currency. Routed studio = studio that most often bid that country.
- **Plausibility:** e.g. site coverage (GFA / site area) and fit-out share against the archive range per typology, with the archive cases shown as evidence.
- **Client match:** fuzzy match against `archive.client` → new or repeat.
- **Gap suggestions:** e.g. missing fit-out area suggested from the typology's archive ratio, marked as a suggestion.
- **Completeness and lead status:** weighted by `field_criticality`; rules from `qualification_rule`.
- **Comparables:** top 3 archive proposals by typology, market and size, with fee/m² converted into the RFP's billing currency, plus outcome and loss reason.

## Pricing in the demo

The archive includes SOG's fee logic (construction cost × fee % × modifiers × stage shares), so an indicative fee could be computed. With 14 synthetic records generated by that same logic, a "model" would just give back the generator's assumptions. Recommendation: show comparables (fee/m² in local currency, outcome, loss reason) and leave the fee estimate to Phase 2. Awaiting confirmation.

## Two-day sequence

Day 1:

1. Repo, Docker Compose (pgvector Postgres with app and `n8n` databases), FastAPI skeleton, Alembic with schema filter.
2. Migration 0001 (provided schema + fixes), migration 0002 (`intake`), archive loader for `v0-synthetic`.
3. RFP pack generator + gold-set ground truth.
4. Ingestion: upload → envelope → dedup → classify → layout (PyMuPDF).
5. Pass 1 extraction with quotes → bounding boxes, `llm_calls` logging, evaluation script → first accuracy number.

Day 2:

1. Pass 2 consolidation and conflict detection.
2. Engine v0 (above).
3. Review UI: inbox, split view with highlights, edit with a reason, confirm, audit trail, comparables panel.
4. Accuracy tuning on the gold set.
5. Demo script and seeded demo pack.

Cut first if time runs short: n8n workflow, archive docx extraction (test 2), proposal mapping screen, admin screens.

## Decisions confirmed 2026-10-05

1. Markets: all of them. Reference data covers the GCC and wider Arab region, India, Singapore, Vietnam, China, Australia and Spain (14 countries, 35 cities).
2. Pricing: comparables only; fee estimate is Phase 2.
3. LibreOffice installed; docx, xlsx and email are rendered to PDF so every source gets a page highlight.
4. We generate the RFP packs (`service/tools/gen_rfp_packs.py`).

## Build status (2026-10-05)

Built and verified without a model key:
- Docker Compose Postgres with separate `sog` and `n8n` databases; n8n behind a compose profile.
- Migrations 0001 (provided schema + fixes), 0002 (`intake`), 0003 (rendered PDF). Up, down and up again verified.
- Loader for reference data and the archive; idempotent (run twice, same counts: 14 proposals, 88 fee lines).
- 14 gold RFP packs + `truth.json`, three layouts, planted traps and 4 GFA conflicts.
- Intake: zip/eml expansion, sha256 dedup to the existing envelope, LibreOffice rendering, PyMuPDF layout lines.
- Extraction pass 1 (one structured-output call per document, line-id citations, quote → highlight boxes), every call logged to `intake.llm_call`.
- Engine (pass 2), 14 unit tests passing: synonyms scoped by country, sq ft/ha conversion with rounding tolerance, conflicts, location tier, billing currency learned from the archive, studio routing, client match, plausibility, gap suggestions, completeness, lead status, comparables.
- API and review UI: inbox, split view with revision-cloud highlights, conflict resolution and edits with a required reason, confirm and lock, proposal sections, reference projects, audit trail, reference data, model calls, accuracy.

Changes from the plan:
- Pass 2 is deterministic code in the engine, not a second model call: the model lists every mention with citations, the engine decides. It is cheaper, repeatable and auditable.
- Classification is part of the per-document extraction call, not a separate call.
- Source documents for RFPs and the archive share `lineage.source_document`; `intake` holds only RFP-specific tables.
- Model: `claude-haiku-4-5` (configurable in `.env` as `SOG_MODEL_EXTRACT`).

Blocked: the first accuracy number needs `ANTHROPIC_API_KEY` in `.env`.
