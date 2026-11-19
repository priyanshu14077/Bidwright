"""Proposals: a confirmed request, priced by the fee engine, as an editable and versioned fee proposal.

    confirmed record ──price──> version 1 (draft) ──edit…──> approve (gate 3, frozen)
                                                     └── new version: a draft copied from the last one

Every change goes through lock_draft (the version must be a draft, and the
caller must have seen its current revision), bumps the revision, and is written
to proposal.event. Approved versions are also frozen by database triggers.
"""

import json
import re
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field as PField
from sqlalchemy import Connection, text

from bidwright import fees
from bidwright.auth import require
from bidwright.config import settings
from bidwright.db import engine
from bidwright.tenancy import Principal

router = APIRouter(prefix="/api")
READ, REVIEW, APPROVE = (Depends(require(p)) for p in ("read", "review", "approve"))

# code, title, kind; the order is the document's order
SECTIONS = (
    ("cover", "Cover", "cover"),
    ("understanding", "Project Understanding", "prose"),
    ("scope", "Scope of Services", "prose"),
    ("deliverables", "Deliverables", "table"),
    ("programme", "Programme", "table"),
    ("fees", "Fee Proposal", "table"),
    ("reimbursables", "Reimbursable Expenses", "table"),
    ("payment", "Payment Schedule", "table"),
    ("exclusions", "Exclusions", "prose"),
    ("terms", "Terms and Conditions", "prose"),
)
COMPUTED = {"fees", "payment"}  # their data comes from the fee lines, never typed in


def dumps(value) -> str:
    return json.dumps(value, default=str)


# ---------------------------------------------------------------- pricing

class PricingChoices(BaseModel):
    """The estimator's pricing choices, on top of the confirmed record."""
    complexity: str | None = None
    cost_type: str | None = None
    construction_cost_usd_m2: float | None = PField(None, gt=0)

    def choices(self) -> dict:
        return {k: v for k, v in self.model_dump(include={"complexity", "cost_type", "construction_cost_usd_m2"})
                .items() if v is not None}


def price(conn: Connection, envelope_id: int, choices: dict):
    """The latest confirmed record for the envelope and the fee engine's estimate for it."""
    opp = conn.execute(text("""SELECT o.version, o.record, o.confirmed_at, e.received_at, e.title
                               FROM intake.opportunity o JOIN intake.envelope e USING (envelope_id)
                               WHERE o.envelope_id = :e ORDER BY o.version DESC LIMIT 1"""),
                       dict(e=envelope_id)).first()
    if not opp:
        raise HTTPException(409, "confirm the request before pricing it")
    params = fees.Parameters(conn)
    issued = opp.record.get("issue_date")
    year = int(str(issued)[:4]) if issued else opp.confirmed_at.year
    try:
        inputs = fees.inputs_from_record(opp.record, params, year, choices)
        archive = fees.load_archive(conn, settings.dataset_version, before=opp.received_at.date())
        est = fees.estimate(inputs, params, archive)
    except fees.MissingInputs as e:
        raise HTTPException(422, {"message": str(e), "missing": e.fields})
    except fees.PricingError as e:
        raise HTTPException(422, {"message": str(e), "missing": []})
    return opp, est, params


# ---------------------------------------------------------------- building a version

def next_reference(conn: Connection, org_id) -> str:
    """<initials>-<year>-<nnnn>, gapless per workspace and year. The advisory lock serialises
    concurrent creates in one workspace until the transaction ends."""
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('proposal-reference:' || :o))"), dict(o=str(org_id)))
    name = conn.execute(text("SELECT name FROM tenancy.organization WHERE org_id = :o"), dict(o=org_id)).scalar()
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name or "P"))[:3].upper()
    prefix = f"{initials}-{date.today().year}-"
    last = conn.execute(text("SELECT max(substring(reference FROM '[0-9]+$')::int) FROM proposal.bid "
                             "WHERE reference LIKE :p || '%'"), dict(p=prefix)).scalar()
    return f"{prefix}{(last or 0) + 1:04d}"


def section_content(code: str, record: dict, est: fees.FeeEstimate, params: fees.Parameters,
                    reference: str) -> tuple[str | None, object, str]:
    """(body, data, origin) for a new section. Prose waits for drafting (step 3), holding what the drafter needs."""
    if code == "cover":
        return None, {
            "client": record.get("client_name"), "project": record.get("project_name"),
            "location": ", ".join(x for x in (record.get("city"), params.country_names.get(est.inputs.country)) if x),
            "reference": reference, "client_reference": record.get("rfp_reference"),
            "date": date.today().isoformat(), "validity_days": int(params.num("terms.validity_days", 90)),
            "payment_days": int(params.num("terms.payment_days", 30)), "currency": est.currency}, "record"
    if code == "deliverables":
        return None, est.deliverables, "engine"
    if code == "programme":
        return None, est.programme, "engine"
    if code == "reimbursables":
        return None, [asdict(r) for r in est.reimbursables], "engine"
    if code in COMPUTED:
        return None, None, "engine"
    inputs = {
        "understanding": {k: record.get(k) for k in ("project_name", "client_name", "typology", "city", "country",
                                                     "gfa_m2", "site_area_m2", "special_requests")},
        "scope": {"services": est.inputs.services, "stages": est.inputs.stages},
        "exclusions": {"excluded_scope": record.get("excluded_scope") or []},
        "terms": {"payment_terms_days": record.get("payment_terms_days") or int(params.num("terms.payment_days", 30)),
                  "liability_cap": record.get("liability_cap"), "special_requests": record.get("special_requests")},
    }
    return None, inputs.get(code), "pending"


def write_engine_content(conn: Connection, version_id: int, record: dict, est: fees.FeeEstimate,
                         params: fees.Parameters, reference: str, keep: set[str] = frozenset()) -> None:
    """Fee lines and sections from an estimate. Sections in keep (edited by hand) are left as they are."""
    conn.execute(text("DELETE FROM proposal.line WHERE version_id = :v"), dict(v=version_id))
    conn.execute(text("""INSERT INTO proposal.line (version_id, line_no, service, stage, basis, basis_qty,
                           basis_unit, engine_amount_local, amount_local, factors)
                         VALUES (:v, :n, :service, :stage, :basis, :basis_qty, :basis_unit, :amount, :amount,
                                 CAST(:factors AS jsonb))"""),
                 [dict(v=version_id, n=i + 1, service=ln.service, stage=ln.stage, basis=ln.basis,
                       basis_qty=ln.basis_qty, basis_unit=ln.basis_unit, amount=ln.amount_local,
                       factors=dumps([asdict(f) for f in ln.factors])) for i, ln in enumerate(est.lines)])
    for seq, (code, title, kind) in enumerate(SECTIONS):
        if code in keep:
            continue
        body, data, origin = section_content(code, record, est, params, reference)
        conn.execute(text("""INSERT INTO proposal.section (version_id, code, seq, title, kind, body, data, origin)
                             VALUES (:v, :code, :seq, :title, :kind, :body, CAST(:data AS jsonb), :origin)
                             ON CONFLICT (version_id, code) DO UPDATE SET body = EXCLUDED.body, data = EXCLUDED.data,
                               origin = EXCLUDED.origin, updated_by = NULL, updated_at = now()"""),
                     dict(v=version_id, code=code, seq=seq, title=title, kind=kind, body=body,
                          data=None if data is None else dumps(data), origin=origin))


def recompute(conn: Connection, version_id: int) -> float:
    """Fee total and payment schedule from the current lines. The advance % is the one the version was priced
    with, so a later settings change does not move an existing proposal."""
    v = conn.execute(text("SELECT estimate FROM proposal.version WHERE version_id = :v"), dict(v=version_id)).one()
    seq = dict(conn.execute(text("SELECT code, seq FROM ref.stage")).all())
    labels = dict(conn.execute(text("SELECT code, label FROM ref.stage")).all())
    lines = conn.execute(text("SELECT stage, amount_local FROM proposal.line WHERE version_id = :v"),
                         dict(v=version_id)).all()
    total = float(sum(a for _, a in lines))
    by_stage: dict[str, float] = {}
    for stage, amount in sorted(lines, key=lambda r: seq.get(r.stage, 99)):
        by_stage[stage] = by_stage.get(stage, 0) + float(amount)
    sched = v.estimate.get("payment_schedule") or []
    advance = float(sched[0]["pct"]) if sched else 10.0
    schedule = [asdict(m) for m in fees.schedule(by_stage, total, advance, labels)]
    conn.execute(text("UPDATE proposal.version SET fee_total_local = :t WHERE version_id = :v"),
                 dict(t=total, v=version_id))
    conn.execute(text("UPDATE proposal.section SET data = CAST(:d AS jsonb), updated_at = now() "
                      "WHERE version_id = :v AND code = 'payment'"), dict(d=dumps(schedule), v=version_id))
    return total


def log(conn: Connection, proposal_id: int, version_id: int | None, action: str, who: Principal,
        target: str | None = None, old=None, new=None, reason: str | None = None) -> None:
    conn.execute(text("""INSERT INTO proposal.event (proposal_id, version_id, action, target, old, new, reason, actor)
                         VALUES (:p, :v, :a, :t, CAST(:old AS jsonb), CAST(:new AS jsonb), :r, :who)"""),
                 dict(p=proposal_id, v=version_id, a=action, t=target, old=None if old is None else dumps(old),
                      new=None if new is None else dumps(new), r=reason, who=who.email))


# ---------------------------------------------------------------- reading

def load_version(conn: Connection, proposal_id: int, version_no: int) -> dict:
    v = conn.execute(text("""SELECT v.*, p.reference, p.title, p.envelope_id FROM proposal.version v
                             JOIN proposal.bid p USING (proposal_id)
                             WHERE v.proposal_id = :p AND v.version_no = :n"""),
                     dict(p=proposal_id, n=version_no)).mappings().first()
    if v is None:
        raise HTTPException(404, "no such proposal version")
    q = lambda sql: [dict(r._mapping) for r in conn.execute(text(sql), dict(v=v["version_id"]))]  # noqa: E731
    sections = q("SELECT * FROM proposal.section WHERE version_id = :v ORDER BY seq")
    reimb = next((s["data"] for s in sections if s["code"] == "reimbursables"), None) or []
    latest_record = conn.execute(text("SELECT max(version) FROM intake.opportunity WHERE envelope_id = :e"),
                                 dict(e=v["envelope_id"])).scalar()
    return {
        **dict(v),
        "stale": latest_record != v["record_version"],
        "reimbursables_total_local": sum(float(r.get("amount_local") or 0) for r in reimb),
        "fee_lines": q("SELECT * FROM proposal.line WHERE version_id = :v ORDER BY line_no"),
        "sections": sections,
        "events": q("SELECT * FROM proposal.event WHERE version_id = :v ORDER BY event_id"),
        "outputs": outputs(conn, v["version_id"]),
    }


def outputs(conn: Connection, version_id: int) -> list[dict]:
    """The files exported from a version, newest first."""
    return [dict(r._mapping) for r in conn.execute(text(
        """SELECT output_id, format, file_name, size_bytes, template, created_by, created_at FROM proposal.output
           WHERE version_id = :v ORDER BY created_at DESC, format"""), dict(v=version_id))]


def lock_draft(conn: Connection, proposal_id: int, version_no: int, revision: int):
    """The draft version, locked for this transaction, if the caller has seen its latest revision."""
    v = conn.execute(text("""SELECT v.*, p.reference FROM proposal.version v JOIN proposal.bid p USING (proposal_id)
                             WHERE v.proposal_id = :p AND v.version_no = :n FOR UPDATE OF v"""),
                     dict(p=proposal_id, n=version_no)).first()
    if v is None:
        raise HTTPException(404, "no such proposal version")
    if v.status != "draft":
        raise HTTPException(409, f"version {version_no} is {v.status}; create a new version to change it")
    if v.revision != revision:
        raise HTTPException(409, {"message": "this version changed since you loaded it; reload and redo your edit",
                                  "your_revision": revision, "current_revision": v.revision})
    return v


def bump(conn: Connection, version_id: int) -> None:
    conn.execute(text("UPDATE proposal.version SET revision = revision + 1, updated_at = now() "
                      "WHERE version_id = :v"), dict(v=version_id))


# ---------------------------------------------------------------- endpoints

class NewProposal(PricingChoices):
    title: str | None = None


@router.post("/envelopes/{envelope_id}/proposals")
def create_proposal(envelope_id: int, body: NewProposal, who: Principal = REVIEW):
    with engine.begin() as conn:
        opp, est, params = price(conn, envelope_id, body.choices())
        reference = next_reference(conn, who.org_id)
        title = body.title or opp.record.get("project_name") or opp.title or reference
        proposal_id = conn.execute(text("""INSERT INTO proposal.bid (reference, envelope_id, title, created_by)
                                           VALUES (:r, :e, :t, :who) RETURNING proposal_id"""),
                                   dict(r=reference, e=envelope_id, t=title, who=who.email)).scalar_one()
        version_id = conn.execute(text("""
            INSERT INTO proposal.version (proposal_id, version_no, record_version, pricing_choices, estimate,
              engine_version, currency, fx_per_usd, engine_total_local, fee_total_local, created_by)
            VALUES (:p, 1, :rv, CAST(:choices AS jsonb), CAST(:est AS jsonb), :ev, :cur, :fx, :total, :total, :who)
            RETURNING version_id"""),
            dict(p=proposal_id, rv=opp.version, choices=dumps(body.choices()), est=dumps(est.to_dict()),
                 ev=est.engine_version, cur=est.currency, fx=est.fx_per_usd, total=est.fee_total_local,
                 who=who.email)).scalar_one()
        write_engine_content(conn, version_id, opp.record, est, params, reference)
        recompute(conn, version_id)
        log(conn, proposal_id, version_id, "create", who, new={"reference": reference, "record_version": opp.version,
                                                              "fee_total_local": est.fee_total_local,
                                                              "choices": body.choices()})
        return load_version(conn, proposal_id, 1)


@router.get("/proposals")
def list_proposals(envelope_id: int | None = None, _: Principal = READ):
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text("""
            SELECT p.proposal_id, p.reference, p.title, p.envelope_id, p.created_at, p.created_by,
                   v.version_no AS latest_version, v.status, v.currency, v.fee_total_local
            FROM proposal.bid p
            JOIN LATERAL (SELECT * FROM proposal.version WHERE proposal_id = p.proposal_id
                          ORDER BY version_no DESC LIMIT 1) v ON true
            WHERE CAST(:e AS bigint) IS NULL OR p.envelope_id = :e
            ORDER BY p.created_at DESC"""), dict(e=envelope_id))]


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: int, _: Principal = READ):
    with engine.connect() as conn:
        p = conn.execute(text("SELECT * FROM proposal.bid WHERE proposal_id = :p"),
                         dict(p=proposal_id)).mappings().first()
        if p is None:
            raise HTTPException(404, "no such proposal")
        versions = conn.execute(text("""SELECT version_no, status, record_version, currency, engine_total_local,
                                          fee_total_local, revision, created_by, created_at, approved_by, approved_at
                                        FROM proposal.version WHERE proposal_id = :p ORDER BY version_no"""),
                                dict(p=proposal_id)).mappings().all()
        events = conn.execute(text("SELECT * FROM proposal.event WHERE proposal_id = :p ORDER BY event_id"),
                              dict(p=proposal_id)).mappings().all()
    return {**dict(p), "versions": [dict(v) for v in versions], "events": [dict(e) for e in events]}


@router.get("/proposals/{proposal_id}/versions/{version_no}")
def get_version(proposal_id: int, version_no: int, _: Principal = READ):
    with engine.connect() as conn:
        return load_version(conn, proposal_id, version_no)


class Edit(BaseModel):
    revision: int
    reason: str | None = None


class SectionEdit(Edit):
    body: str | None = None
    data: list | dict | None = None


@router.patch("/proposals/{proposal_id}/versions/{version_no}/sections/{code}")
def edit_section(proposal_id: int, version_no: int, code: str, body: SectionEdit, who: Principal = REVIEW):
    if body.data is not None and code in COMPUTED:
        raise HTTPException(422, f"the {code} table is calculated from the fee lines; edit the lines instead")
    with engine.begin() as conn:
        v = lock_draft(conn, proposal_id, version_no, body.revision)
        old = conn.execute(text("SELECT body, data, origin FROM proposal.section WHERE version_id = :v AND code = :c"),
                           dict(v=v.version_id, c=code)).mappings().first()
        if old is None:
            raise HTTPException(404, f"no section {code}")
        new = {"body": body.body if body.body is not None else old["body"],
               "data": body.data if body.data is not None else old["data"]}
        conn.execute(text("""UPDATE proposal.section SET body = :body, data = CAST(:data AS jsonb), origin = 'estimator',
                               updated_by = :who, updated_at = now() WHERE version_id = :v AND code = :c"""),
                     dict(body=new["body"], data=None if new["data"] is None else dumps(new["data"]),
                          who=who.email, v=v.version_id, c=code))
        bump(conn, v.version_id)
        log(conn, proposal_id, v.version_id, "edit_section", who, code, dict(old), new, body.reason)
        return load_version(conn, proposal_id, version_no)


class LineEdit(Edit):
    amount_local: float = PField(ge=0)
    note: str | None = None


@router.patch("/proposals/{proposal_id}/versions/{version_no}/lines/{line_no}")
def edit_line(proposal_id: int, version_no: int, line_no: int, body: LineEdit, who: Principal = REVIEW):
    with engine.begin() as conn:
        v = lock_draft(conn, proposal_id, version_no, body.revision)
        old = conn.execute(text("SELECT amount_local, note FROM proposal.line WHERE version_id = :v AND line_no = :n"),
                           dict(v=v.version_id, n=line_no)).mappings().first()
        if old is None:
            raise HTTPException(404, f"no fee line {line_no}")
        conn.execute(text("UPDATE proposal.line SET amount_local = :a, note = COALESCE(:note, note) "
                          "WHERE version_id = :v AND line_no = :n"),
                     dict(a=body.amount_local, note=body.note, v=v.version_id, n=line_no))
        total = recompute(conn, v.version_id)
        bump(conn, v.version_id)
        log(conn, proposal_id, v.version_id, "edit_line", who, f"line {line_no}", dict(old),
            {"amount_local": body.amount_local, "note": body.note, "fee_total_local": total}, body.reason)
        return load_version(conn, proposal_id, version_no)


class NewLine(Edit):
    service: str
    stage: str
    amount_local: float = PField(ge=0)
    note: str


@router.post("/proposals/{proposal_id}/versions/{version_no}/lines")
def add_line(proposal_id: int, version_no: int, body: NewLine, who: Principal = REVIEW):
    """A line the engine could not price (for example a stage it has no share for)."""
    with engine.begin() as conn:
        v = lock_draft(conn, proposal_id, version_no, body.revision)
        line_no = conn.execute(text("SELECT coalesce(max(line_no), 0) + 1 FROM proposal.line WHERE version_id = :v"),
                               dict(v=v.version_id)).scalar()
        try:
            with conn.begin_nested():
                conn.execute(text("""INSERT INTO proposal.line (version_id, line_no, service, stage, basis,
                                       amount_local, note) VALUES (:v, :n, :s, :st, 'estimator', :a, :note)"""),
                             dict(v=v.version_id, n=line_no, s=body.service, st=body.stage, a=body.amount_local,
                                  note=body.note))
        except Exception:
            raise HTTPException(422, "unknown service or stage")
        total = recompute(conn, v.version_id)
        bump(conn, v.version_id)
        log(conn, proposal_id, v.version_id, "add_line", who, f"line {line_no}", None,
            dict(body.model_dump(exclude={"revision", "reason"}), fee_total_local=total), body.reason)
        return load_version(conn, proposal_id, version_no)


@router.delete("/proposals/{proposal_id}/versions/{version_no}/lines/{line_no}")
def delete_line(proposal_id: int, version_no: int, line_no: int, revision: int, reason: str,
                who: Principal = REVIEW):
    with engine.begin() as conn:
        v = lock_draft(conn, proposal_id, version_no, revision)
        old = conn.execute(text("DELETE FROM proposal.line WHERE version_id = :v AND line_no = :n "
                                "RETURNING service, stage, amount_local"),
                           dict(v=v.version_id, n=line_no)).mappings().first()
        if old is None:
            raise HTTPException(404, f"no fee line {line_no}")
        total = recompute(conn, v.version_id)
        bump(conn, v.version_id)
        log(conn, proposal_id, v.version_id, "delete_line", who, f"line {line_no}", dict(old),
            {"fee_total_local": total}, reason)
        return load_version(conn, proposal_id, version_no)


class Reprice(Edit, PricingChoices):
    pass


@router.post("/proposals/{proposal_id}/versions/{version_no}/reprice")
def reprice(proposal_id: int, version_no: int, body: Reprice, who: Principal = REVIEW):
    """Price again from the latest confirmed record and current settings. Replaces every fee line (hand edits
    included) and the engine's tables; sections edited by hand are kept."""
    with engine.begin() as conn:
        v = lock_draft(conn, proposal_id, version_no, body.revision)
        envelope_id = conn.execute(text("SELECT envelope_id FROM proposal.bid WHERE proposal_id = :p"),
                                   dict(p=proposal_id)).scalar_one()
        opp, est, params = price(conn, envelope_id, body.choices())
        kept = set(conn.execute(text("SELECT code FROM proposal.section WHERE version_id = :v AND origin = 'estimator'"),
                                dict(v=v.version_id)).scalars())
        old = {"record_version": v.record_version, "fee_total_local": v.fee_total_local,
               "choices": v.pricing_choices}
        conn.execute(text("""UPDATE proposal.version SET record_version = :rv, pricing_choices = CAST(:c AS jsonb),
                               estimate = CAST(:est AS jsonb), engine_version = :ev, currency = :cur, fx_per_usd = :fx,
                               engine_total_local = :t WHERE version_id = :v"""),
                     dict(rv=opp.version, c=dumps(body.choices()), est=dumps(est.to_dict()), ev=est.engine_version,
                          cur=est.currency, fx=est.fx_per_usd, t=est.fee_total_local, v=v.version_id))
        write_engine_content(conn, v.version_id, opp.record, est, params, v.reference, keep=kept)
        total = recompute(conn, v.version_id)
        bump(conn, v.version_id)
        log(conn, proposal_id, v.version_id, "reprice", who, None, old,
            {"record_version": opp.version, "fee_total_local": total, "choices": body.choices(),
             "kept_sections": sorted(kept)}, body.reason)
        return load_version(conn, proposal_id, version_no)


def approval_problems(conn: Connection, v) -> list[str]:
    """Gate 3: what stops this draft being approved."""
    problems = []
    empty = conn.execute(text("""SELECT title FROM proposal.section WHERE version_id = :v AND kind = 'prose'
                                 AND coalesce(trim(body), '') = '' ORDER BY seq"""), dict(v=v.version_id)).scalars().all()
    if empty:
        problems.append(f"sections not written yet: {', '.join(empty)}")
    gaps = conn.execute(text("""SELECT title FROM proposal.section WHERE version_id = :v
                                AND position('[to be written]' IN coalesce(body, '')) > 0 ORDER BY seq"""),
                        dict(v=v.version_id)).scalars().all()
    if gaps:
        problems.append(f"sections with gaps marked [to be written]: {', '.join(gaps)}")
    if not v.fee_total_local or v.fee_total_local <= 0:
        problems.append("the fee total is zero")
    latest = conn.execute(text("SELECT max(o.version) FROM intake.opportunity o JOIN proposal.bid p "
                               "USING (envelope_id) WHERE p.proposal_id = :p"), dict(p=v.proposal_id)).scalar()
    if latest != v.record_version:
        problems.append(f"the request was re-confirmed (record version {latest}) after this version was priced "
                        f"(version {v.record_version}); reprice it first")
    return problems


@router.post("/proposals/{proposal_id}/versions/{version_no}/approve")
def approve(proposal_id: int, version_no: int, body: Edit, who: Principal = APPROVE):
    with engine.begin() as conn:
        v = lock_draft(conn, proposal_id, version_no, body.revision)
        problems = approval_problems(conn, v)
        if problems:
            raise HTTPException(409, {"message": "this version cannot be approved yet", "problems": problems})
        superseded = conn.execute(text("""UPDATE proposal.version SET status = 'superseded', superseded_at = now()
                                          WHERE proposal_id = :p AND status = 'approved' RETURNING version_no"""),
                                  dict(p=proposal_id)).scalars().all()
        conn.execute(text("""UPDATE proposal.version SET status = 'approved', approved_by = :who, approved_at = now(),
                               revision = revision + 1, updated_at = now() WHERE version_id = :v"""),
                     dict(who=who.email, v=v.version_id))
        log(conn, proposal_id, v.version_id, "approve", who, None, None,
            {"fee_total_local": v.fee_total_local, "superseded_versions": superseded}, body.reason)
        return load_version(conn, proposal_id, version_no)


class NewVersion(BaseModel):
    reason: str | None = None


@router.post("/proposals/{proposal_id}/versions")
def new_version(proposal_id: int, body: NewVersion, who: Principal = REVIEW):
    """A new draft, copied from the latest version. That version is left exactly as it was."""
    with engine.begin() as conn:
        conn.execute(text("SELECT 1 FROM proposal.bid WHERE proposal_id = :p FOR UPDATE"), dict(p=proposal_id))
        src = conn.execute(text("SELECT * FROM proposal.version WHERE proposal_id = :p ORDER BY version_no DESC LIMIT 1"),
                           dict(p=proposal_id)).first()
        if src is None:
            raise HTTPException(404, "no such proposal")
        if src.status == "draft":
            raise HTTPException(409, f"version {src.version_no} is still a draft; edit that one")
        new_id = conn.execute(text("""
            INSERT INTO proposal.version (proposal_id, version_no, based_on, record_version, pricing_choices, estimate,
              engine_version, currency, fx_per_usd, engine_total_local, fee_total_local, created_by)
            SELECT proposal_id, version_no + 1, version_id, record_version, pricing_choices, estimate, engine_version,
                   currency, fx_per_usd, engine_total_local, fee_total_local, :who
            FROM proposal.version WHERE version_id = :src RETURNING version_id"""),
            dict(src=src.version_id, who=who.email)).scalar_one()
        conn.execute(text("""INSERT INTO proposal.line (version_id, line_no, service, stage, basis, basis_qty,
                               basis_unit, engine_amount_local, amount_local, factors, note)
                             SELECT :new, line_no, service, stage, basis, basis_qty, basis_unit, engine_amount_local,
                                    amount_local, factors, note FROM proposal.line WHERE version_id = :src"""),
                     dict(new=new_id, src=src.version_id))
        conn.execute(text("""INSERT INTO proposal.section (version_id, code, seq, title, kind, body, data, origin,
                               citations, updated_by, updated_at)
                             SELECT :new, code, seq, title, kind, body, data, origin, citations, updated_by, updated_at
                             FROM proposal.section WHERE version_id = :src"""),
                     dict(new=new_id, src=src.version_id))
        log(conn, proposal_id, new_id, "new_version", who, None, {"from_version": src.version_no},
            {"version_no": src.version_no + 1}, body.reason)
        return load_version(conn, proposal_id, src.version_no + 1)
