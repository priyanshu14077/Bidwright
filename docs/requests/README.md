# Sample requests for checking prequalification

Twenty invented requests at three levels of scope, in the kinds of document clients actually send: an email with a
PDF brief and an Excel area schedule, WhatsApp chats, call notes, and a WhatsApp screenshot. Use them to check
gate 1 (Qualify, Clarify or Decline), what is found, estimated or missing, the questions to ask, and the fee card.

Every client, project and person is invented. The dates sit around the demo week: the requests arrive on
**Friday 20 November 2026**, so deadlines land where the rules can see them.

## How to use them

- **Ask screen:** drop all the files of one request into the chat box (the paperclip or the upload button), or
  paste a `.txt` file's contents as a message.
- **Requests → Add files:** add all the files of one request folder together, so they become one request.
- Change a sample and regenerate: `cd service && uv run python ../docs/requests/make_requests.py`.

A workspace holds each file once, so a request you have already added comes back as the same request. To add
it again, delete it first or change a word in the file.

## 1. Complete scope: expect **Qualify**

Everything pricing needs is stated. Gate 1 should suggest Qualify, the fee card should price straight away, and
only checks (flags) should appear, never questions about the scope.

| Request | Documents | What it checks | Result (20 Nov run) |
|---|---|---|---|
| **A. Wadi Hanifa Offices**, Riyadh | Email + PDF brief + Excel area schedule | Reading three documents as one request; areas from the schedule; fit-out area for interior design; SAR as asked; a project in Saudi Arabia handled by the Dubai studio | Qualify · SAR 6,690,000 · flag: other country |
| **B. Marina Crest tower**, Dubai | WhatsApp chat | A **repeat client** (two past proposals); architecture and landscape; AED as asked; an exact deadline | Qualify · AED 3,034,000 · flag: repeat client |
| **C. Qamar Corniche Hotel**, Doha | Call notes | Notes rather than a document; hotel (keys, fit-out); concept to design development; QAR as asked | Qualify · QAR 17,240,000 · flag: other country |

## 2. Partial scope: expect **Clarify**

Enough to start, but something pricing depends on is missing, estimated or unclear. Gate 1 should suggest
Clarify, show the estimate with where it came from, and write the questions for the client.

| Request | Documents | What it checks | Result (20 Nov run) |
|---|---|---|---|
| **A. Lusail offices** | WhatsApp chat | A rough ask: no client name, no site area (**estimated** from past projects), no currency (**QAR estimated** from the practice's work in Qatar, so it asks); "by Thursday next week" worked out as **Thu 26 Nov** | Clarify · too little information, currency to confirm · flags: tight deadline, other country |
| **B. Al Mouj villas**, Muscat | Email | Site area in hectares (6 ha = 60,000 m²) but **no floor area**, so the fee cannot be priced yet; fee asked in USD; services and stages not stated (practice defaults) | Clarify · too little information · fee card asks for the GFA |
| **C. Seef Bay**, Manama | Screenshot (PNG) + call notes | Reading a **screenshot**; two figures for the floor area (30,000 or about 24,000 m²) that the client must confirm; **uncapped liability**; BHD as asked | Clarify · floor area unclear · flags: uncapped liability, other country |

## 3. Thin or out of scope: expect **Clarify** or **Decline**

| Request | Documents | What it checks | Result (20 Nov run) |
|---|---|---|---|
| **A. One line** | WhatsApp chat | "What do you charge for designing a building?": almost nothing to go on | Clarify · too little information · fee card lists what it needs |
| **B. Ancoats museum**, Manchester | Email | A country the practice does not serve (United Kingdom) | **Decline** · not one of our markets |
| **C. Mussafah warehouse**, Abu Dhabi | Email | Structural and MEP engineering only: services the practice does not offer | Clarify (expected **Decline**: see the known gap below) |
| **D. Corniche offices**, Jeddah | Email | Submissions close **Mon 23 Nov**, one working day after it arrived | **Decline** · deadline too close |

## Known gap found with these samples

**C. Mussafah warehouse** should be declined but is not. The reader only reports services that are on the
practice's list, so "structural and MEP engineering" is dropped, the request looks like it named no service, and
the practice default (Architecture) fills the gap. The rule "None of the services asked for is one the practice
offers" never sees it. The fix is for the reader to report every service asked for, mark the ones not on the
list, and for the default to apply only when no service was named at all. It changes the reading prompt, so it
should be re-run against the evaluation set (`make eval-intake`) before the demo.

Fixed with these samples: the same deadline written as "12:00 Riyadh time" in the email and "12:00" in the
brief was read as two different times and shown as a conflict; it is now one deadline.

## Also worth checking on each

- **Where each value came from:** every found value's source pill quotes the words it was read from; open it.
- **Fee card:** the range, the won-bid range, the checks, and "See every rule this fee used" (How we price).
- **Chat:** ask "Why is this fee what it is?" and open "How I got this" on the answer.
- **Chats:** the conversation is kept with the request it read.
