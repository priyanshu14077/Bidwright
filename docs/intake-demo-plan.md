# Intake qualification, sources, mobile and chat

Status: 2026-11-20, slices 1–6 built on branch `intake-demo` (not committed); the first screen is the chat (Ask), with Decide, Requests and Set up. Demo recorded as a Loom video at the weekend.

## Goal

A non-technical operator drops in whatever the client sent (a WhatsApp line, an email, a full pack), sees in one screen what is known, what was estimated and what is missing, asks the client, and gets one fee card. A director approves, overrides or rejects it from a phone. Every number and conclusion shows where it came from. The same workflow can be driven from chat.

What the client said on the review call: the engine and the proposal split were right; the screens were overwhelming; requests arrive in very uneven shape; and "why is it providing the figure it's providing?" must have a visible answer.

## Non-negotiables

- The model reads, extracts, asks and explains. It never produces a fee. Every figure comes from the engine and traces back to comparables or settings.
- Live Claude calls in the demo, through the existing Anthropic setup, unchanged.
- Required fields, weights, rules, estimate methods and stage defaults are data or config, not code.
- Alembic owns the database shape; Prisma is introspection only.
- Intake channels, storage, OCR and auth stay behind interfaces. WhatsApp is paste, upload or forward, not a live integration.
- Branding stays Bidwright, with the fictional practice Northbeam Studio. The client's name appears nowhere in the code, data or UI.

## Decisions

| Question | Decision |
|---|---|
| Completeness | A deterministic weighted checklist from config (`field_criticality`, extended with currency, tier, client status and deadline). The model only labels each field found, ambiguous or missing, writes the follow-up questions and writes the "why" lines. |
| Qualification | A recommendation, never a verdict: **Go**, **Clarify** or **No-go**, from rule rows in the workspace's data. The most severe rule that fires sets it. Flags (repeat client, uncapped liability, below minimum fee, tight deadline, cross-entity) inform but don't decide. A person chooses Qualify, Ask client or Decline; a decline needs a reason; both are logged. India is served through the Singapore studio. |
| Missing values | Only the engine estimates, from comparables, with a method per field in config: GFA from plot × floor-area ratio, else floors × floor plate, else the comparables' median; plot from GFA ÷ ratio; interior and landscape areas from ratios; stages from the default package for the kind of ask (feasibility → concept, otherwise concept and schematic design); services default to architecture. Each estimate records its method, the comparables used, the 25th–75th percentile spread and a confidence (low, medium, high). Fewer than 2 comparables: no estimate, pricing stops and names the field. |
| Uncertainty | Price once at the central estimates. Re-price each numeric estimate at its low and high value; combine the swings with the won-bid range by root-sum-of-squares. The fee card shows the won-bid band and the wider band from assumptions, with each assumption's share. Stage and service assumptions don't widen the range; the card says what was priced and what adding a stage would cost. |
| Currency | The currency the practice has used in that market (from the market data), not the project's local currency. Vietnam is quoted in USD. |
| Sources | Every conclusion (field value, estimate, flag, rule hit, fee factor, chat claim) links to at least one `lineage.evidence` row: `text` (document line or message span), `reply` (the client's answer), `comparable`, `rule` or `setting`, with a quote and a one-line why. Quotes are checked word for word against the source; a quote that doesn't match is rejected. The API refuses to return a conclusion without evidence. |
| Showing sources | The source pill from the design rules: sage dot for quoted (channel and exact words), clay dot for estimated (number of projects and confidence in words), no pill for missing (the question instead). Tapping opens a bottom sheet on a phone and a popover on desktop with the quote highlighted, the why line and Edit value. Pasted text renders as text, not as a PDF. |
| Chat | A server-side tool-use loop on `claude-sonnet-5-5` (new `model_chat` setting) that calls the same service functions as the screens, with the user's permissions and audit trail. Read tools answer; proposing tools return a pending action shown as a confirm card, and only the person's tap executes it. Field updates from chat also need a confirm. Answers must cite evidence ids from this turn's tool results, and every figure must appear in a tool result. History in `chat.thread`, `chat.message`, `chat.pending_action`. |
| Design | The design rules and stylesheet in `frontend/public/inspo/` (git-ignored), copied in with a `bw-` class prefix. Caprasimo and Figtree from Google Fonts; `lucide-react` icons. Mobile-first with one breakpoint at 1024 px. |
| Fee card | Opening fee from the engine, then **"Won bids landed"** (where similar won bids sat against the engine's figure). No "expected agreed fee": the archive has no negotiated fees to support it. |
| Mobile-first screens | Drop in a request; What we have (gate 1); Fee card (gate 2); Approval (director); Chat. Plus the source and reason sheets. The proposal editor, extraction tables, PDF viewer, Settings, Backtests and Members stay as they are, under Details or the rail. |
| Request set | A new versioned set, `data/gold/v1-requests/`, next to the untouched `v0-synthetic`. Claude writes the message wording once from a spec; the wording is committed; `generate.py` renders every file from it with a fixed seed and no model call. Ground truth is written beside each case. |

## Demo script (Loom, about 12 minutes)

Desktop in one window, the director's view in a 390 px Chrome window beside it, the same session in both.

1. **WhatsApp one-liner, Dubai, repeat client.** Paste: *"Hi, Marina Crest again, they want concept for a resi tower in JVC, ~30 floors, can you send a number by Thu?"* What we have: found (client, matched as repeat; Dubai; residential; concept; Thursday), estimated (GFA from 30 floors × comparables' floor plate, low confidence; AED), missing (plot area, interior scope) with questions. Recommendation: Clarify. Copy the questions for WhatsApp. Price anyway: wide range labelled with its 2 assumptions, top 3 comparables, flags, why per factor.
2. **The client replies, answered in chat.** *"Plot is 2,400 m² and they want interiors for the amenity floors."* Two proposed field updates, each sourced to the reply; confirm. Completeness rises, the range narrows, the recommendation becomes Go. *"Why this fee?"* lists the factors with their sources.
3. **Feasibility ask.** Email: Singapore studio asked for a *"rough number for a 4-acre plot in Pune, mixed use, quote in USD"*. Cross-entity routing, GFA estimated from plot × floor-area ratio, flags for feasibility only and USD billing. Recommendation: Clarify. Decline with "unpaid feasibility"; the decision is logged.
4. **Full pack, approved on a phone.** A 12-document pack: almost everything found, one GFA conflict between the area schedule and the main RFP shown side by side. Pick the value, qualify. The director, at 390 px, opens the fee card, overrides to a round figure with a reason, approves, and the PDF is exported. The audit trail on the desktop shows both.

Close: every number came from the engine, and every value shows where it came from.

## Request set (`data/gold/v1-requests/`)

| Kind | Cases |
|---|---|
| WhatsApp: 3 one-liners, 2 short threads, 1 voice-note transcript, 1 screenshot | 7 |
| Email: a forwarded chain, a partial attachment, "see attached" with the wrong attachment | 3 |
| Feasibility asks | 3 |
| Call or meeting notes | 3 |
| Repeat client, "similar to project X" | 3 |
| Revision of an earlier request | 3 |
| Mixed language or cross-entity | 3 |
| Full packs, taken from the 14 existing packs | 3 |
| **Total** | **28 (25 new)** |

Markets: UAE, Singapore, India, Australia and Vietnam, plus at least one outside the practice's markets. The four demo requests are among them.

Ground truth per case: each field's status (found, ambiguous, missing) and value; the expected recommendation and the rules that should fire; the fields the follow-up questions must cover.

`make eval SET=v1-requests` reports field-status accuracy, missing-field recall and false alarms (target recall ≥ 0.9), recommendation accuracy, and question coverage. `--rescore` re-scores without model calls.

## Slices

Each slice ends with proof (tests, eval numbers, 390 px screenshots of every screen it touched) and waits for review. Work happens on a new branch from `proposal-generation-steps-1-5`.

| # | Slice | Demo | Proof |
|---|---|---|---|
| 1 | **Request set and evaluation.** `cases.json`, the wording, `generate.py`, the truth file; the evaluation scores field status, recommendation and question coverage. | Must | The set regenerates byte for byte; eval runs on the current engine to give a baseline. |
| 2 | **Intake for messages and screenshots.** Paste-text endpoint with a channel (WhatsApp, email, notes), stored as a source document with character spans; screenshots read through Claude's image input behind the reading interface; extraction handles informal messages and cited hints (floors, plot in acres). | Must | Tests; the 7 WhatsApp cases extract with cited spans. |
| 3 | **Gate 1.** `lineage.evidence` (migration 0009); field status; estimates from comparables with method, spread and confidence; rules for Go, Clarify, No-go and flags; follow-up questions (one model call); `GET /api/envelopes/{id}/gate1` and the decision endpoint (qualify, ask, decline with a reason). | Must | Tests including "no conclusion without evidence"; eval numbers on the set. |
| 4 | **Pricing with assumptions.** The fee engine accepts estimates; low and high re-pricing; two-band range; stage add-on cost; "won bids landed"; the fee card API with factors and evidence; approve, override and reject with reasons. | Must | Tests; the four demo requests priced with their ranges. |
| 5 | **Guided screens.** The `bw-` stylesheet, fonts and icons; the shell (rail, tab bar); Drop in a request, What we have, Fee card, Approval; source pills, sheets and popovers; reason sheets; the old screens under Details. | Must | 390 px and desktop screenshots of each screen; build and type check. |
| 6 | **Chat.** Migration 0011 (`chat` schema); the tool loop; read and proposing tools; confirm cards; citation and figure checks; the panel on desktop, the sheet on a phone. | Must | Tests with a fake model; a live run of demo step 2. |
| 7 | **Demo rehearsal.** Load the four demo requests into the demo workspace, run the script end to end with live calls, fix what breaks, write the Loom checklist. | Must | A full run of the script, timed. |

Nice to have, in the order to cut them: chat streaming; restyling the Details screens; the 36-document pack; an "add to home screen" app manifest; an expected agreed fee once the client's round-by-round fees are loaded; bottom-up pricing (team × days × seniority × market).

If time runs short inside the must-haves, cut in this order: the screenshot request (keep it in the set, read it as pasted text), the revision cases, demo step 3's cross-entity flag, chat field updates (answer questions on screen instead).

## Not in this iteration

- Live WhatsApp or email integration.
- The client's own request schema (comes after discovery; the schema file is the only place to change).
- Win probability, client credit or strategic fit scoring: no data to support them.
