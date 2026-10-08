# Northbeam Studio: sample archive

> **Synthetic data.** Northbeam Studio is a fictional practice. This archive seeds the demo workspace so a new user can see Bidwright working before loading their own proposals. Every client, reference and project name is invented.

## What's here

| File | What it is |
|---|---|
| `archive.json` | 14 past proposals, nested: one object per proposal, plus studios, clients, benchmarks, fee assumptions and FX |
| `proposals/*.docx` | The 14 proposal documents, in Northbeam's house layout |

The loader (`bidwright.archive.loader`) reads both into the workspace's `archive` tables.

## The 14 proposals

| ID | Studio | Market | Typology | Services | Status |
|---|---|---|---|---|---|
| P01 | Dubai | Dubai, prime | Residential tower | Arch, Interior | Won |
| P02 | Dubai | Ras Al Khaimah, second tier | Hospitality resort | Arch, Interior, Landscape | Won |
| P03 | Dubai | Riyadh, prime | Mixed use | Arch, Landscape | Lost (price) |
| P04 | Dubai | Al Ain, second tier | Villa community | Masterplan, Arch, Landscape | Won |
| P05 | Barcelona | Barcelona, prime | Office refurbishment | Arch, Interior | Lost (local heritage firm) |
| P06 | Barcelona | Malaga, second tier | Boutique hotel | Arch, Interior, Landscape | Won |
| P07 | Singapore | Singapore, prime | Grade A office | Arch | Withdrawn (on hold) |
| P08 | Singapore | Singapore, prime | Condominium | Arch, Interior, Landscape | Won |
| P09 | Ho Chi Minh City | HCMC, prime | Mixed-use tower | Arch, Interior, Landscape | Won (with project challenges) |
| P10 | Ho Chi Minh City | Da Nang, second tier | Resort + masterplan | All four | Submitted |
| P11 | Guangzhou | Guangzhou, prime | Office HQ campus | Arch, Landscape | Lost (price) |
| P12 | Guangzhou | Foshan, second tier | New district | Masterplan | Won |
| P13 | Dubai | Mumbai, prime | Twin residential towers | Arch, Interior | Lost (fee too high) |
| P14 | Dubai | Dubai, prime | Mid-rise office | Arch, Interior | Submitted |

The set covers:
- **All five typologies and all four services,** across six stage types.
- **Seven currencies:** AED, SAR, EUR, SGD, USD, CNY, INR.
- **Prime and second-tier locations,** new and repeat clients, and low, medium and high complexity.
- **Won, lost, withdrawn and submitted outcomes,** with loss reasons and one project-challenge record.

## How the fees were built

Fees follow a typical practice pricing method in three layers:

1. **Construction cost per m² (cited).** Sources are Turner & Townsend market intelligence 2025 for Dubai, Riyadh, Singapore, Ho Chi Minh City, Mumbai and China, and a secondary source citing Turner & Townsend for Guangzhou and Madrid (used as the Barcelona proxy).
   - Where a city has no asset-type figure, the city average is multiplied by typology ratios derived from Dubai's asset-type costs.
   - Costs are escalated to the proposal year using the cited inflation rates (UAE 5%, China 1%, others the global 3.8%).
2. **Fee parameters (assumptions).** These include:
   - Full-service fee % by typology, set within the cited Dubai range of 2–8%.
   - A scale effect, and stage shares informed by the cited RIBA-aligned and villa fee splits.
   - Market factors for international design-led work in lower-cost markets.
   - Interior design as a % of fit-out cost (cited range 10–20% for luxury studios, set lower for large-scale work).
   - Landscape at 9% of landscape cost (cited range 7–12%).
   - Masterplan rates per hectare.
   - Render and trip rates.

   **These are the numbers a real workspace replaces** when it calibrates on its own archive.
3. **Realism.** Each quote has random variation of roughly ±6%, and lost bids are priced 10–18% above the model, so the engine has real signal to find. Every proposal's generation steps are in `pricing_trace`. Every benchmark carries its URL and every assumption its rationale in `archive.json`.

## Deliberate variations in the documents

The documents are not all written the same way, so the normaliser has something to normalise. The JSON holds the clean values, which serve as the ground truth:

- **P13 (Mumbai)** states areas in **sq ft**.
- **Stage names vary:**
  - "Scheme Design (Anteproyecto)" (P05)
  - "Basic Design / Design Development" (P09)
  - "Preliminary Design (DD)" (P11)
  - "GFC Drawing Review" (P02)

  `ref.term_synonym` maps each of these to the controlled stage codes.
- **Site area** is written in hectares for large sites and m² for small ones.
- **Seven currencies,** with conversion via `ref.fx_rate`. The AED and SAR rates are the official pegs; the others are illustrative and should be replaced with dated rates.

This turns the pack into a **mini gold set**: extract each document, compare the result with the JSON, and measure accuracy.

## Limitations

- Fee parameters are informed assumptions, not market facts. Masterplan rates per hectare have no reliable public benchmark.
- Fourteen records are enough to exercise the pipeline, not to train or validate a pricing model.
- `archive.pricing_trace` exists only for synthetic data.
