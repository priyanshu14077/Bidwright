# Proposal generation and export

Status: 2026-11-18, steps 1–5 and the pricing settings done on branch `proposal-generation`.

## Goal

A confirmed request becomes a full fee proposal in the firm's house layout. The estimator reviews and edits it in Bidwright, an admin approves it, and the approved version is exported as DOCX and PDF.

## Decisions

| Question | Decision |
|---|---|
| Where fees come from | A deterministic fee engine prices every service and stage from the workspace's fee assumptions and records the factors behind each line. The estimator can edit any line. |
| Prose sections | Claude drafts Project Understanding and the client-specific parts of Scope and Terms from the confirmed record and the matching sections of the closest past proposals, and cites them. Standard scope, exclusions and terms text comes from a library per service and stage. |
| Flow | Generate a draft, edit it section by section (every change logged and versioned), then approve (gate 3). Only approved versions can be exported, and approval freezes the version. |
| Output | DOCX from a per-workspace Word template, with Northbeam's layout as the default, plus PDF from LibreOffice headless. |
| Roles | Estimators generate and edit drafts. Admins approve and export. |
| Missing inputs | Pricing stops and names the missing fields. The engine guesses only cost type, complexity and client status, and lists each guess in `inputs.assumed`. |

## Proposal layout (from the 14 archive documents)

Cover block (client, location, reference, date, validity), then:

1. Project Understanding: prose, plus the areas table.
2. Scope of Services, per service and per stage.
3. Deliverables.
4. Programme.
5. Fee Proposal (service × stage).
6. Reimbursable Expenses.
7. Payment Schedule.
8. Exclusions.
9. Terms and Conditions, with a table of client-requested amendments.

Sign-off at the end.

## Steps

1. **Fee engine** (done). `service/bidwright/fees.py`, endpoint `GET /api/envelopes/{id}/fees`, `make fees`.
2. **Proposal model** (done). Schema `proposal`: `bid`, `version`, `line`, `section`, `event`, `output`, with row-level security and freeze triggers; API in `service/bidwright/proposals.py`.
3. **Drafting** (done). `service/bidwright/drafting.py`, `POST /api/proposals/{id}/versions/{n}/draft`, `make draft-smoke`.
4. **Proposal screen** (done). `frontend/src/pages/Proposal.tsx`, the review page's Proposals tab, and Settings.
5. **Export** (done). `service/bidwright/export.py`, `POST /api/proposals/{id}/versions/{n}/export`, `GET /api/outputs/{id}`.
6. **Templates.** Per-workspace template upload, validated for the placeholders it needs. Northbeam's template is the default.

## Step 1: fee engine

The method is the one documented in `data/archive/v0-synthetic/README.md`:

- Construction cost per m² comes from the city's benchmark market. A direct asset-type figure is used where there is one; otherwise the market average × typology ratio. The cost is escalated to the proposal year.
- The architecture fee % by use, × (GFA / 50,000)^-0.1, × the market factor.
- Modifiers: location tier, repeat client, complexity.
- Stage shares.
- Interior: fit-out area × (ratio × building cost) × 9% × market factor, priced to Design Development.
- Landscape: area × the market band's cost × 9%, with no market factor.
- Masterplan: rate per hectare, tapered above 50 ha, × 0.8 in low-cost markets, × a stage factor.
- Reimbursables:
  - Render quantity is the median of similar past proposals.
  - Site trips are added when the studio is in a different country from the project.
- Payment schedule: 10% advance, then the rest in proportion to the stage fees.
- Range: how far the firm's won bids sat from this model, using only proposals that were already in the archive when the RFP arrived.

All parameters are workspace data (see Pricing settings below).

**Backtest** (`make fees`). Each archive fee divided by the model equals that proposal's recorded random variation × lost-bid premium (`tests/test_fees.py`). In other words, the engine reproduces the method exactly.

| Status | n | Actual / model |
|---|---|---|
| Won | 7 | 0.97–1.09 (median 1.00) |
| Lost | 4 | 1.03–1.17 (median 1.10) |

## Step 2: proposal model

A proposal (`proposal.bid`, reference `<initials>-<year>-<nnnn>`) belongs to one request and has numbered versions. Version 1 is created by pricing the latest confirmed record. The version stores that record's version number, the estimator's pricing choices and the engine's full output, so a later change to settings never moves it.

| Endpoint | Who | Does |
|---|---|---|
| `POST /api/envelopes/{id}/proposals` | estimator | Prices the request and creates version 1 (draft): fee lines, the cover from the record, engine tables, and prose sections marked pending with their inputs |
| `GET /api/proposals`, `/api/proposals/{id}`, `/api/proposals/{id}/versions/{n}` | viewer | Read; a version says whether the request was re-confirmed since (`stale`) |
| `PATCH …/sections/{code}`, `PATCH`/`POST`/`DELETE …/lines` | estimator | Edit a draft. Line changes recompute the total and the payment schedule; the engine's amount stays beside the edited one |
| `POST …/reprice` | estimator | Price again from the latest record and settings; replaces lines, keeps sections edited by hand |
| `POST …/approve` | admin | Gate 3: refused while a prose section is empty, the total is zero, or the request was re-confirmed since pricing. Supersedes the previously approved version |
| `POST /api/proposals/{id}/versions` | estimator | New draft copied from the latest version; one draft at a time |

- **Consistency:** every edit carries the version's `revision`, and a stale one gets 409.
- **Logging:** every action is written to `proposal.event`.
- **Freezing:** triggers refuse any direct change to an approved or superseded version, or to its lines and sections. Deletes that cascade from removing a proposal or a workspace are still allowed. Migration 0003 also lets cascade deletes through the lineage append-only trigger, so a workspace with confirmed requests can be deleted.

## Step 3: drafting

`POST /api/proposals/{id}/versions/{n}/draft` writes the four prose sections, by default only those still pending. Pass `sections` to redraft specific ones.

| Section | Standard text (`ref.clause`, from the starter pack) | Claude adds |
|---|---|---|
| Project Understanding | (none) | 1–3 paragraphs about the project; the facts table is built from the record |
| Scope of Services | One paragraph per service and stage | Requirements the RFP asks for beyond that, each citing the RFP line |
| Exclusions | The standard list, plus record exclusions not already covered (word-overlap match) | (none) |
| Terms and Conditions | Standard paragraph, sign-off, and the firm's position per clause | The client's requested changes, each citing the RFP line; liability and payment deviations come straight from the record |

- **The call.** One structured-output call to `settings.model_draft` (Claude Opus 5.5, medium effort, server-side refusal fallback) through `llm.structured_call`. It is logged in `intake.llm_call` and costs about $0.06 per proposal.
  - Inputs: the record the version was priced from, every RFP line with its id, the three closest past proposals (`engine.comparables`), and the standard text.
- **Checks before anything is written.** The answer is sent back once with the problems if:
  - a cited id was not given;
  - an item has no source;
  - a scope addition or amendment does not cite an RFP line;
  - a figure above 31 is in neither the record nor the RFP;
  - it names another client or project from a past proposal.
- **Concurrency.** The model runs outside any transaction. The result is written only if the version's revision is unchanged (otherwise 409), and the action is logged as a `draft` event with the call, cost and notes for the estimator.
- **Approval.** Approval also refuses a section containing `[to be written]`, which marks a service and stage with no standard text.

## Step 5: export

An admin exports an approved (or superseded) version. Drafts are refused.

1. `export.context` builds the template's values from the frozen version:
   - cover, fee lines and totals, the payment schedule, reimbursables, programme, deliverables, and payment days (recorded on the cover when the proposal is created);
   - the four written sections, converted from Markdown into Word paragraphs in the template's own styles: headings to Heading 2, `**bold**`, bullets to List Bullet, and pipe tables to Table Grid tables.
2. `docxtpl` fills `data/templates/proposal-v0.docx`, generated by `data/templates/generate_default_template.py` (`make template`). The script's docstring lists every placeholder.
3. `reading.office_to_pdf` converts the result with LibreOffice, one conversion at a time.
4. Both files go to the blob store, and each is recorded in `proposal.output` (append-only, migration 0006 adds `blob_uri` and `size_bytes`) with the template's id. An `export` event is logged.
5. Exporting the same version with the same template again returns the existing files.

`tests/test_export.py` opens the produced DOCX to check it (no placeholder left, nine numbered headings, fee table total, Markdown styles, header) and reads the PDF's text.

## Pricing settings (per workspace)

Every new workspace is seeded with all of these from the starter pack, and the loader fills gaps in existing workspaces without overwriting their edits. `GET /api/settings/pricing` returns them with a readiness list; admins edit them through `PUT /api/settings/pricing/...`, and every edit is written to `ref.setting_change`.

| Setting | Table | Starter source | Needed for |
|---|---|---|---|
| Fee method: fee % by use, scale exponent, stage shares, modifiers, interior / landscape / masterplan rates, render and trip prices, advance %, programme weeks, default render count, validity and payment days | `ref.fee_assumption` (45 values) | `data/reference/v0.json` `fee_method` | every price |
| Typology cost ratios | `ref.typology_cost_ratio` | `v0.json` | a cost type missing from the cost book |
| Markets, per country: cost band, fee factor, cost escalation % | `ref.market` (14) | `data/reference/cost-book-v0.json` | every price; a country with no row cannot be priced |
| Construction cost book: USD/m² per city (or country-wide) and cost type, with price year, basis and source | `ref.construction_cost` (343) | `cost-book-v0.json` | architecture and interior |
| Studios | `ref.studio` | none for a new workspace | site trips in reimbursables |
| Locations and tiers | `ref.location` | `v0.json` | the tier modifier, city aliases |
| FX rates | `ref.fx_rate` (shared) | `v0.json` | fees in the billing currency |

**The cost book is synthetic.** `data/reference/generate_cost_book.py` (`make cost-book`) writes it.
- The nine benchmark markets carry the published averages the archive was priced from.
- Cities the archive priced on another market are proxies of that market (for example, Guangzhou on the China national average).
- All other cities are a stated factor of a benchmark (Sharjah = 0.88 × Dubai), or an invented average (Doha, Sydney).
- Each row records its basis (benchmark, derived, proxy, synthetic or estimator) and source.

The engine looks up the city's figure for the cost type, then the city's average × the typology ratio, then the same for the country-wide row; if none exists it stops and asks for a figure. It escalates from the figure's price year at the market's rate, and the fee line's factor states which row it used.

## Open points

- Cost type (villa or high rise, 3-star or 5-star) and complexity are not in the request schema. They stay estimator choices on the fee screen (step 4) unless extraction should propose them.
- Studios are not part of the starter pack. A new workspace adds its own, otherwise site trips are never priced. They need a settings screen.
- The settings API has no screen yet. Build it with the proposal screen in step 4, or sooner.
- Exports in production: the server image (not written yet) must install LibreOffice and set `BIDWRIGHT_LIBREOFFICE` to its `soffice`. Conversions already run one at a time.
