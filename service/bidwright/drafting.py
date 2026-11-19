"""Drafting the proposal's prose sections.

    confirmed record ─┐
    RFP documents ────┼─> one Claude call (structured output) ──> validate ──> assemble with the
    past proposals ───┤        understanding, scope additions,      (retry once     workspace's standard
    standard text ────┘        client-requested amendments           on failure)    text (ref.clause)

Scope, exclusions and terms start from the workspace's standard text, word for
word. Claude writes only what is specific to the request: the Project
Understanding narrative, scope the RFP asks for beyond the standard text, and
the client's requested changes to the firm's terms. Everything it writes cites
its sources (record fields, RFP lines, past proposals). The answer is rejected
and asked for again if it cites something it was not given, states a figure
that is in neither the record nor the RFP, or names another client.
"""

import json
import re
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection, text

from bidwright import llm, proposals
from bidwright.auth import require
from bidwright.config import settings
from bidwright.db import engine
from bidwright.engine import comparables
from bidwright.extraction import document_lines, render_for_model
from bidwright.tenancy import Principal

PROMPT_VERSION = "draft-v2"
PROSE = ("understanding", "scope", "exclusions", "terms")
NEEDS_MODEL = {"understanding", "scope", "terms"}
TO_BE_WRITTEN = "[to be written]"
SERVICE_ORDER = ("architecture", "interior_design", "landscape", "masterplan")
FACTS = (("gfa_m2", "Gross floor area (GFA)", "m²"), ("site_area_m2", "Site area", "m²"),
         ("landscape_area_m2", "Landscape area", "m²"), ("fitout_area_m2", "Interior design area", "m²"),
         ("keys", "Hotel keys", ""), ("units", "Residential units", ""))
SMALL_NUMBER = 31  # counts, days of the month: too common to demand a source for

router = APIRouter(prefix="/api")
REVIEW = Depends(require("review"))


# ---------------------------------------------------------------- sources

@dataclass
class Sources:
    firm: str
    record: dict
    services: list[str]
    stages: list[str]
    documents: dict[str, dict]           # "doc281" -> {document_id, file_name, lines: {id: text}, boxes, rendered}
    past: list[dict]                     # {reference, title, client, sections: {code: body}}
    scope_text: dict[tuple[str, str], str]
    exclusions: list[str]
    terms: dict[str, str]                # standard, signoff
    positions: dict[str, tuple[str, str]]  # clause -> (label, position)
    stage_labels: dict[str, str]
    service_labels: dict[str, str]
    payment_days: int

    def source_ids(self) -> set[str]:
        ids = {f"record.{k}" for k, v in self.record.items() if v not in (None, "", [])}
        ids |= {f"{d}/{line}" for d, doc in self.documents.items() for line in doc["lines"]}
        return ids | {f"past.{p['reference']}" for p in self.past}

    def grounding_numbers(self) -> set[float]:
        """Every figure the request itself states: the record and the RFP documents."""
        blob = json.dumps(self.record) + " " + " ".join(" ".join(d["lines"].values()) for d in self.documents.values())
        return set(numbers(blob))

    def other_clients(self) -> set[str]:
        """Names from past proposals that must not appear in this client's text."""
        own = (self.record.get("client_name") or "").lower()
        names = set()
        for p in self.past:
            if p["client"] and p["client"].lower() not in own and own not in p["client"].lower():
                names.add(p["client"])
            names.add(p["title"].split(" - ")[0])
        return {n for n in names if len(n) > 3 and n.lower() not in json.dumps(self.record).lower()}

    def fill(self, s: str) -> str:
        return s.replace("{firm}", self.firm).replace("{payment_days}", str(self.payment_days))


def gather(conn: Connection, version) -> Sources:
    record = conn.execute(text("""SELECT o.record FROM intake.opportunity o JOIN proposal.bid b USING (envelope_id)
                                  WHERE b.proposal_id = :p AND o.version = :v"""),
                          dict(p=version.proposal_id, v=version.record_version)).scalar_one()
    envelope_id = conn.execute(text("SELECT envelope_id FROM proposal.bid WHERE proposal_id = :p"),
                               dict(p=version.proposal_id)).scalar_one()
    documents = {}
    for doc_id, file_name in conn.execute(text("""SELECT document_id, file_name FROM lineage.source_document
                                                  WHERE envelope_id = :e ORDER BY document_id"""), dict(e=envelope_id)):
        lines = document_lines(conn, doc_id)
        if lines:
            documents[f"doc{doc_id}"] = dict(document_id=doc_id, file_name=file_name,
                                             lines={ln["id"]: ln["text"] for ln in lines},
                                             boxes={ln["id"]: (ln["page"], ln["bbox"]) for ln in lines},
                                             rendered=render_for_model(lines))
    past = []
    for item in comparables(conn, envelope_id, limit=3)["items"]:
        row = conn.execute(text("""SELECT p.proposal_id, c.name FROM archive.proposal p
                                   LEFT JOIN archive.client c ON c.client_id = p.client_id
                                     AND c.dataset_version = p.dataset_version
                                   WHERE p.reference = :r"""), dict(r=item["reference"])).first()
        sections = dict(conn.execute(text("""SELECT section_code, body FROM archive.proposal_section
                                             WHERE proposal_id = :p AND section_code IN ('understanding', 'terms')"""),
                                     dict(p=row.proposal_id)).all())
        past.append(dict(reference=item["reference"], title=item["title"], client=row.name, sections=sections))
    clauses = conn.execute(text("SELECT kind, key, service, stage, label, body FROM ref.clause ORDER BY kind, seq")).all()
    firm = conn.execute(text("SELECT o.name FROM tenancy.organization o JOIN proposal.bid b USING (org_id) "
                             "WHERE b.proposal_id = :p"), dict(p=version.proposal_id)).scalar_one()
    payment_days = conn.execute(text("SELECT value FROM ref.fee_assumption WHERE key = 'terms.payment_days'")).scalar()
    inputs = version.estimate["inputs"]
    return Sources(
        firm=firm, record=record, services=inputs["services"], stages=inputs["stages"], documents=documents, past=past,
        scope_text={(c.service, c.stage): c.body for c in clauses if c.kind == "scope"},
        exclusions=[c.body for c in clauses if c.kind == "exclusion"],
        terms={c.key: c.body for c in clauses if c.kind == "terms"},
        positions={c.key: (c.label, c.body) for c in clauses if c.kind == "position"},
        stage_labels=dict(conn.execute(text("SELECT code, label FROM ref.stage")).all()),
        service_labels=dict(conn.execute(text("SELECT code, label FROM ref.service")).all()),
        payment_days=int(payment_days or 30))


# ---------------------------------------------------------------- the model call

SYSTEM = """You draft parts of an architecture and design practice's fee proposal, for its estimator to review.

You are given the client's confirmed request (the record), the RFP documents line by line, the closest past
proposals from the practice's archive, and the practice's standard text. Write in the practice's voice: plain,
specific, professional British English, third person ("the practice" or its name), no marketing language.

Rules:
- Cite every paragraph and item with the ids of what supports it: record.<field> for the confirmed record,
  docN/<line id> for an RFP line (for example doc281/p1:15), past.<reference> for a past proposal whose wording
  you followed. Use only ids you were given.
- Figures (areas, keys, dates, money) come from the record. Where an RFP document states a different figure,
  the record is what was confirmed: use the record's.
- Past proposals are for tone and structure only. Never copy their facts, names or figures into this proposal.
- Project Understanding is about the project: the client, the site and building, the brief, and the services and
  stages proposed. Leave exclusions, deadlines and commercial terms out of it; they have their own sections.
- Do not restate the standard text. Scope additions are only what the RFP explicitly asks for beyond the standard
  scope for the services and stages in the record. Amendments are only where the client's documents ask for terms
  that differ from the practice's standard positions; quote or closely paraphrase the client's request.
- If something the proposal needs is missing or unclear, say so in notes for the estimator; do not guess.
"""


def schema(sources: Sources) -> dict:
    cited = {"type": "array", "items": {"type": "string"}}
    paragraph = {"type": "object", "additionalProperties": False, "required": ["text", "sources"],
                 "properties": {"text": {"type": "string"}, "sources": cited}}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["understanding", "scope_additions", "amendments", "notes"],
        "properties": {
            "understanding": {"type": "array", "items": paragraph,
                              "description": "1 to 3 paragraphs: who the client is, what they are asking for, "
                                             "where, and what the proposal covers"},
            "scope_additions": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["service", "text", "sources"],
                "properties": {"service": {"type": "string", "enum": [*sources.services, "general"]},
                               "text": {"type": "string"}, "sources": cited}}},
            "amendments": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["clause", "label", "client_request", "sources"],
                "properties": {"clause": {"type": "string", "enum": [*sources.positions, "other"]},
                               "label": {"type": "string", "description": "the clause name, for clause other"},
                               "client_request": {"type": "string"}, "sources": cited}}},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
    }


def prompt(sources: Sources) -> str:
    docs = "\n\n".join(f"### {d} ({doc['file_name']}); cite a line as {d}/<line id>\n{doc['rendered']}"
                       for d, doc in sources.documents.items()) or "(no documents)"
    past = "\n\n".join(f"### past.{p['reference']}: {p['title']}\n" + "\n".join(
        f"[{code}]\n{body}" for code, body in p["sections"].items()) for p in sources.past) or "(none)"
    scope = "\n".join(f"- {sources.service_labels.get(s, s)}, {sources.stage_labels.get(st, st)}: {t}"
                      for (s, st), t in sources.scope_text.items() if s in sources.services and st in sources.stages)
    positions = "\n".join(f"- {k} ({label}): {sources.fill(body)}" for k, (label, body) in sources.positions.items())
    return f"""The practice: {sources.firm}

<record>
{json.dumps(sources.record, indent=1, default=str)}
</record>

<rfp_documents>
{docs}
</rfp_documents>

<past_proposals>
{past}
</past_proposals>

<standard_scope>
{scope or "(none for these services and stages)"}
</standard_scope>

<standard_terms>
{sources.fill(sources.terms.get("standard", ""))}
Standard positions by clause:
{positions}
</standard_terms>

Draft the Project Understanding paragraphs, any scope additions, and any client-requested amendments."""


def numbers(s: str) -> list[float]:
    return [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s)]


def validator(sources: Sources):
    ids = sources.source_ids()
    grounded = sources.grounding_numbers()
    others = sources.other_clients()

    def check(data: dict) -> None:
        problems = []
        if not 1 <= len(data["understanding"]) <= 4:
            problems.append("write 1 to 3 understanding paragraphs")
        items = [("understanding", p["text"], p["sources"]) for p in data["understanding"]]
        items += [("scope addition", a["text"], a["sources"]) for a in data["scope_additions"]]
        items += [("amendment", a["client_request"], a["sources"]) for a in data["amendments"]]
        for where, txt, cited in items:
            if not cited:
                problems.append(f"{where} has no sources: {txt[:60]!r}")
            unknown = [c for c in cited if c not in ids]
            if unknown:
                problems.append(f"{where} cites ids you were not given: {unknown[:5]}")
            invented = [n for n in numbers(txt) if n > SMALL_NUMBER and n not in grounded]
            if invented:
                problems.append(f"{where} states figures that are in neither the record nor the RFP: {invented[:5]}")
            leaked = [n for n in others if n.lower() in txt.lower()]
            if leaked:
                problems.append(f"{where} names another project or client from a past proposal: {leaked}")
        for where, entries in (("scope addition", data["scope_additions"]), ("amendment", data["amendments"])):
            for e in entries:
                if not any("/" in c for c in e["sources"]):
                    problems.append(f"each {where} must cite the RFP line that asks for it")
        clauses = [a["clause"] for a in data["amendments"] if a["clause"] != "other"]
        if len(clauses) != len(set(clauses)):
            problems.append("one amendment per clause")
        if any(a["clause"] == "other" and not a["label"].strip() for a in data["amendments"]):
            problems.append("an amendment with clause other needs a label")
        if problems:
            raise llm.ValidationFailed("; ".join(problems))
    return check


def call_model(sources: Sources, trace_id: uuid.UUID) -> llm.Result:
    """One structured call; seam for tests."""
    return llm.structured_call(run_id=None, trace_id=trace_id, model=settings.model_draft,
                               prompt_version=PROMPT_VERSION, system=SYSTEM,
                               content=[{"type": "text", "text": prompt(sources)}], schema=schema(sources),
                               effort="medium", max_tokens=16000, validate=validator(sources))


# ---------------------------------------------------------------- assembling sections

@dataclass
class Drafted:
    body: str
    data: object
    origin: str
    citations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def resolve(sources: Sources, cited: list[str]) -> list[dict]:
    """Source ids with something a reviewer can check: the RFP line's text, the field's value, the past proposal."""
    out = []
    for c in cited:
        if c.startswith("record."):
            out.append(dict(id=c, kind="record", quote=json.dumps(sources.record.get(c[7:]), default=str)))
        elif c.startswith("past."):
            p = next((p for p in sources.past if p["reference"] == c[5:]), None)
            out.append(dict(id=c, kind="past_proposal", quote=p["title"] if p else None))
        else:
            d, line = c.split("/", 1)
            doc = sources.documents.get(d, {})
            page, bbox = doc.get("boxes", {}).get(line, (None, None))
            out.append(dict(id=c, kind="rfp", document=doc.get("file_name"), document_id=doc.get("document_id"),
                            page=page, rects=[bbox] if bbox else None, quote=doc.get("lines", {}).get(line)))
    return out


def fmt(n) -> str:
    return f"{float(n):,.0f}"


def understanding(sources: Sources, paragraphs: list[dict]) -> Drafted:
    facts = [(label, f"{fmt(sources.record[k])} {unit}".strip()) for k, label, unit in FACTS if sources.record.get(k)]
    body = "\n\n".join(p["text"].strip() for p in paragraphs)
    if facts:
        body += "\n\n| Item | Description |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in facts)
    return Drafted(body, {"facts": facts}, "claude",
                   [dict(part=f"paragraph {i + 1}", sources=resolve(sources, p["sources"]))
                    for i, p in enumerate(paragraphs)])


def scope(sources: Sources, additions: list[dict]) -> Drafted:
    parts, warnings = [], []
    services = sorted(sources.services, key=lambda s: SERVICE_ORDER.index(s) if s in SERVICE_ORDER else 9)
    for n, service in enumerate(services, start=1):
        parts.append(f"### 2.{n} {sources.service_labels.get(service, service)}")
        for stage in sources.stages:
            label = sources.stage_labels.get(stage, stage)
            txt = sources.scope_text.get((service, stage))
            if txt is None:
                warnings.append(f"no standard scope text for {service} at {label}")
                txt = TO_BE_WRITTEN
            parts.append(f"**{label}:** {txt}")
    if additions:
        parts.append(f"### 2.{len(services) + 1} Project-specific requirements")
        parts += [f"- {a['text'].strip()}" for a in additions]
    return Drafted("\n\n".join(parts), {"additions": additions}, "claude" if additions else "library",
                   [dict(part=f"requirement {i + 1}", sources=resolve(sources, a["sources"]))
                    for i, a in enumerate(additions)], warnings)


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2 and w not in {"and", "the", "for", "design"}}


def similar(a: str, b: str, threshold: float = 0.5) -> bool:
    """Jaccard overlap of content words: 'MEP / structural engineering' matches the standard
    'Structural, MEP and civil engineering design'."""
    wa, wb = words(a), words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= threshold


def exclusions(sources: Sources) -> Drafted:
    items = list(sources.exclusions)
    from_record = []
    for e in sources.record.get("excluded_scope") or []:
        if not any(similar(e, i) for i in items):
            items.append(e)
            from_record.append(e)
    return Drafted("\n".join(f"- {i}" for i in items), {"items": items, "added_from_record": from_record},
                   "library", [dict(part=f"item: {e}", sources=resolve(sources, ["record.excluded_scope"]))
                               for e in from_record])


def record_amendments(sources: Sources) -> list[dict]:
    """Deviations the confirmed record already states; these do not depend on the model."""
    out = []
    if str(sources.record.get("liability_cap") or "").lower() in ("uncapped", "unlimited"):
        out.append(dict(clause="liability_cap", label="", client_request="Uncapped liability",
                        sources=["record.liability_cap"]))
    days = sources.record.get("payment_terms_days")
    if days and int(days) > sources.payment_days:
        out.append(dict(clause="payment_terms", label="", client_request=f"{int(days)} days from invoice",
                        sources=["record.payment_terms_days"]))
    return out


def terms(sources: Sources, amendments: list[dict]) -> Drafted:
    stated = record_amendments(sources)
    rows = stated + [a for a in amendments if a["clause"] == "other"
                     or a["clause"] not in {s["clause"] for s in stated}]
    table = []
    for a in rows:
        label, position = sources.positions.get(a["clause"], (a["label"], "To be agreed with the client"))
        table.append(dict(clause=a["clause"], label=label or a["label"], client_request=a["client_request"].strip(),
                          position=sources.fill(position)))
    body = sources.fill(sources.terms.get("standard", ""))
    if table:
        body += (f"\n\n#### Client-requested amendments\n\n| Clause | Client request | {sources.firm} position |\n"
                 "|---|---|---|\n" + "\n".join(f"| {t['label']} | {t['client_request']} | {t['position']} |"
                                              for t in table))
    body += "\n\n" + sources.fill(sources.terms.get("signoff", "For and on behalf of {firm}"))
    return Drafted(body, {"amendments": table}, "claude" if amendments else "library",
                   [dict(part=f"amendment: {t['label']}", sources=resolve(sources, a["sources"]))
                    for t, a in zip(table, rows)])


def build(sources: Sources, model: dict | None, codes: set[str]) -> dict[str, Drafted]:
    model = model or {"understanding": [], "scope_additions": [], "amendments": []}
    out = {}
    if "understanding" in codes:
        out["understanding"] = understanding(sources, model["understanding"])
    if "scope" in codes:
        out["scope"] = scope(sources, model["scope_additions"])
    if "exclusions" in codes:
        out["exclusions"] = exclusions(sources)
    if "terms" in codes:
        out["terms"] = terms(sources, model["amendments"])
    return out


# ---------------------------------------------------------------- endpoint

class DraftRequest(BaseModel):
    revision: int
    sections: list[str] | None = None  # default: the prose sections still pending
    reason: str | None = None


@router.post("/proposals/{proposal_id}/versions/{version_no}/draft")
def draft(proposal_id: int, version_no: int, body: DraftRequest, who: Principal = REVIEW):
    """Draft prose sections. The model runs outside any transaction; the result is written only if nobody
    changed the version meanwhile (same revision), otherwise 409 and nothing is written."""
    with engine.connect() as conn:
        v = conn.execute(text("SELECT * FROM proposal.version WHERE proposal_id = :p AND version_no = :n"),
                         dict(p=proposal_id, n=version_no)).first()
        if v is None:
            raise HTTPException(404, "no such proposal version")
        if v.status != "draft" or v.revision != body.revision:
            proposals.lock_draft(conn, proposal_id, version_no, body.revision)  # raises the right 409
        pending = set(conn.execute(text("SELECT code FROM proposal.section WHERE version_id = :v AND origin = 'pending'"),
                                   dict(v=v.version_id)).scalars())
        codes = set(body.sections) if body.sections else pending & set(PROSE)
        if not codes:
            raise HTTPException(409, "no sections are pending; name the sections to redraft")
        if codes - set(PROSE):
            raise HTTPException(422, f"only these sections are drafted: {', '.join(PROSE)}")
        sources = gather(conn, v)

    trace_id, result = uuid.uuid4(), None
    if codes & NEEDS_MODEL:
        if not settings.anthropic_api_key:
            raise HTTPException(503, "drafting needs ANTHROPIC_API_KEY in .env")
        try:
            result = call_model(sources, trace_id)
        except llm.ValidationFailed as e:
            raise HTTPException(502, {"message": "the draft failed its checks twice; nothing was written",
                                      "detail": str(e), "trace_id": str(trace_id)})
    drafted = build(sources, result.data if result else None, codes)

    with engine.begin() as conn:
        v = proposals.lock_draft(conn, proposal_id, version_no, body.revision)
        for code, d in drafted.items():
            conn.execute(text("""UPDATE proposal.section SET body = :body, data = CAST(:data AS jsonb), origin = :origin,
                                   citations = CAST(:cit AS jsonb), updated_by = :who, updated_at = now()
                                 WHERE version_id = :v AND code = :code"""),
                         dict(body=d.body, data=json.dumps(d.data), origin=d.origin, cit=json.dumps(d.citations),
                              who=who.email, v=v.version_id, code=code))
        proposals.bump(conn, v.version_id)
        call = conn.execute(text("SELECT call_id, model, cost_usd, input_tokens, output_tokens FROM intake.llm_call "
                                 "WHERE call_id = :c"), dict(c=result.call_id)).mappings().first() if result else None
        report = {"sections": sorted(drafted), "trace_id": str(trace_id),
                  "notes": result.data.get("notes", []) if result else [],
                  "warnings": [w for d in drafted.values() for w in d.warnings],
                  "llm_call": dict(call) if call else None, "attempts": result.attempts if result else 0}
        proposals.log(conn, proposal_id, v.version_id, "draft", who, ",".join(sorted(drafted)), None, report,
                      body.reason)
        return {**proposals.load_version(conn, proposal_id, version_no), "draft": report}
