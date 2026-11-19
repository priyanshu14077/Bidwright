# Demo script: Bidwright for Northbeam Studio (Loom, about 6 minutes)

One path through the product: a client's WhatsApp arrives, Bidwright says what is missing and why, the client
answers, the request is qualified and priced against the practice's own history, the fee is approved, and the
proposal goes out. Every number on screen can be traced to where it came from.

## Before recording

1. `make demo-reset` (asks first; reads three sample requests, a few cents). It clears conversations and test
   requests and leaves the archive, pricing settings and gold sets alone.
2. `make eval` once if the Backtests page is empty (a few minutes, about $1–2).
3. Sign in as the demo user, light theme, browser at 1440 × 900, menu expanded, dossier open.
4. Have two files ready to drag or paste from `docs/requests/2-partial-scope/A - WhatsApp, rough ask/`:
   `WhatsApp Chat - Lusail offices.txt` and `followups/WhatsApp reply - Lusail offices.txt`.

If a take goes wrong, run `make demo-reset ARGS=--yes` and start again.

## The path

| # | Time | Do | Say (the point) |
|---|---|---|---|
| 1 | 0:00 | **Ask** screen, empty. | "Requests arrive however clients send them: WhatsApp, email, call notes, files. You start here." |
| 2 | 0:20 | Paste the Lusail WhatsApp into the box and send. "Reading the request…" shows with its timer. | "It reads the message the way an estimator would." |
| 3 | 0:45 | The answer appears; the **dossier** slides in on the right. Point at the ring, *Needs review*, the missing and estimated rows. Click a source chip: the chat scrolls to the quote. | "Each value says where it came from. The site area is estimated from four past projects, and it says so. Nothing is invented." |
| 4 | 1:30 | Click **Which currency should we quote?** under the chat. | "Qatar is quoted in QAR in this practice's history, but the studio works in AED, so it asks rather than assumes." |
| 5 | 2:00 | Footer: **Ask the client**. Confirm the card. Copy the questions. | "One click gives a WhatsApp-ready message. A person confirms every decision." |
| 6 | 2:30 | Paste the client's reply (`followups/…`) with "The client replied:". Confirm. The dossier updates: *Ready to price*. | "The reply fills the gaps; the request is read again." |
| 7 | 3:00 | Footer: **Qualify the request**, confirm; then open **How I got this** on the last answer and scrub the trace. | "Every answer shows what it did: which tools, how long, and a final check that every figure has a source." |
| 8 | 3:40 | **Open the request → Fee**. Point at the opening fee, won-bid range, the checks. Click **See every rule this fee used**. | "The fee is the practice's own method, and every rule and number behind it is in one register." |
| 9 | 4:30 | On How we price, open one entry (office fee %) and read *On this fee*. | "This 3% is the practice's setting; here is exactly what it did to this fee." |
| 10 | 5:00 | Back to the fee: **Approve**. **Proposal** step: write it, export Word or PDF. | "Approved figures flow into the proposal; nothing is retyped." |
| 11 | 5:40 | **Chats**: the conversation is kept with the request it read and decided. **Backtests**: recommendation accuracy on 28 sample requests. | "It is measured: on requests with known answers, it recommends right every time." |

## Also worth showing if time allows

- **Decline**: open the Corniche offices request (deadline one working day away): *Decline suggested*, with the reason.
- **Repeat client**: drop `docs/requests/1-complete-scope/B - WhatsApp, repeat client/…`: "Repeat client" and their past proposals.
- **A change with history**: under Set up, change one fee setting with a reason; How we price then shows who changed it, when and why.

## Avoid on camera

- The Mussafah warehouse sample (engineering only): it is not yet declined as it should be.
- The Seef Bay sample: it also fires the rough-number rule because of the words "feasibility study".
