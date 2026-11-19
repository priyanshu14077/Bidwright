# v1-requests: incoming requests with known answers

Fictional requests for Northbeam Studio in the shape practices actually receive them. `v0-synthetic` holds formal RFP packs; this set holds everything else: a WhatsApp line, a voice note, a screenshot, a forwarded chain, an email with the wrong attachment, call notes, a feasibility ask, a revision. It tests whether intake sees what a request says, what it leaves out, and what to do next.

| Kind | Requests |
|---|---|
| WhatsApp: one-liners (W01–W03), threads (W04–W05), a voice-note transcript (W06), a screenshot (W07) | 7 |
| Email: forwarded chain (E01), partial attachment (E02), wrong attachment (E03) | 3 |
| Feasibility asks (F01–F03) | 3 |
| Call or meeting notes (N01–N03) | 3 |
| Repeat client, "similar to project X" (R01–R03) | 3 |
| Revisions (V01–V03) | 3 |
| Mixed language or cross-entity (X01–X03) | 3 |
| Full packs reissued from `v0-synthetic` P08, P13, P02 (K01–K03) | 3 |

Demo requests: W01 (step 1; `followups/` holds the client's reply for step 2), F01 (step 3), K01 (step 4).

## Files

| Path | What it is |
|---|---|
| `truth.json` | Every request: kind, channel, receiving studio, received time, its files, and the truth. Also the policy the expected outcomes follow |
| `texts/` | The wording of every message and document, written once and committed |
| `generate.py` | Renders `requests/` from `texts/` and `truth.json`: emails, PDF attachments, the screenshot, reissued packs. No model calls, no clock: `make requests` gives byte-identical files |
| `requests/<id>/` | The files exactly as they arrive, ingested by the evaluation |
| `runs/` | Evaluation reports (git-ignored) |

## The truth

For each request and each of 12 fields (client, client status, country, city, typology, services, stages, GFA, site area, quality tier, billing currency, deadline):

- **found**: the request states it; the value is the right one.
- **ambiguous**: stated unclearly, or two sources disagree (`conflicts` names those).
- **missing**: not stated. A hint records any clue (e.g. "~30 floors").
- **referenced**: taken from a named past project ("same scope as Bay Square").

Expected per request: the recommendation (go, clarify, no-go), the rules that should fire, informational flags, and the fields the follow-up questions must cover. `service/tests/test_request_set.py` re-derives every expected recommendation from the policy in `truth.json`, so the truth cannot drift from the policy.

## Running it

```bash
make eval-intake                    # ingest and read every request (live model calls, about $0.25)
make eval-intake ARGS=--rescore     # score the last run again, no model calls
make eval-intake ARGS="--cases W01 F01"
```
