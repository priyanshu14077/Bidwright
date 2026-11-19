"""Gate 2, the fee card: one figure from the engine, how far it could move, and the director's decision.

    gate 1 assessment ──> pricing values (what the request says, plus the engine's estimates as labelled
    assumptions) ──> fee engine ──> opening fee
                                ──> each numeric assumption priced at its low and high ──> swings
    won-bid range (inner band) + swings, combined by root-sum-of-squares ──> outer band

Nothing here is written by a model. Every figure comes from bidwright.fees and every assumption points to the
gate 1 evidence behind it.

Approving (or overriding) confirms the record with its assumptions, creates the proposal and records the
decision; an override scales the fee lines to the director's figure, with the reason on every line.
"""

import math
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field as PField
from sqlalchemy import Connection, text

from bidwright import drafting, export, fees, gate1, proposals, record
from bidwright.auth import require
from bidwright.config import settings
from bidwright.db import engine
from bidwright.engine import comparables
from bidwright.schema import fields as schema_fields
from bidwright.tenancy import Principal

NUMERIC = ("gfa_m2", "site_area_m2", "fitout_area_m2", "landscape_area_m2")
STAGE_LADDER = ("feasibility", "concept", "schematic_design", "design_development")

router = APIRouter(prefix="/api")
READ, APPROVE = Depends(require("read")), Depends(require("approve"))


def setting_source(why: str) -> dict:
    return {"id": None, "kind": "setting", "why": why}


def pricing_values(conn: Connection, envelope_id: int) -> tuple[dict, list[dict], dict]:
    """What the fee is priced on: the record's values, with each gap filled by the gate 1 estimate and listed
    as an assumption (with its evidence)."""
    row = conn.execute(text("SELECT assessment FROM intake.assessment WHERE envelope_id = :e"),
                       dict(e=envelope_id)).first()
    if row is None:
        raise HTTPException(409, "this request has not been read yet")
    a = row.assessment
    rec = record.build(conn, envelope_id)
    values = {k: v["value"] for k, v in rec.items() if not k.startswith("_")}
    labels = {n: f.label for n, f in schema_fields().items()}
    ev = gate1.evidence_rows(conn, [i for f in a["fields"] for i in f["evidence"]]
                             + [i for x in a.get("extra_estimates", {}).values() for i in x["evidence"]])
    assumptions = []
    for f in a["fields"]:
        if f["status"] in ("estimated", "ambiguous") and f["value"] not in (None, [], ""):
            values[f["name"]] = f["value"]
            assumptions.append(dict(field=f["name"], label=f["label"], status=f["status"], display=f["display"],
                                    range_display=f.get("range_display"), estimate=f.get("estimate"), note=f.get("note"),
                                    evidence=[ev[i] for i in f["evidence"] if i in ev]))
    for name, x in a.get("extra_estimates", {}).items():
        if values.get(name) in (None, [], ""):
            values[name] = x["estimate"]["value"]
            assumptions.append(dict(field=name, label=labels.get(name, name), status="estimated",
                                    display=x["display"], range_display=x.get("range_display"), estimate=x["estimate"],
                                    note=None, evidence=[ev[i] for i in x["evidence"] if i in ev]))
    if values.get("country") and not values.get("location_tier"):
        values["location_tier"] = "second"
        why = f"{values.get('city') or 'The city'} is not on the workspace's location list; priced as a second-tier location."
        assumptions.append(dict(field="location_tier", label="Location tier", status="estimated", display="Second tier",
                                range_display=None, estimate=None, note=None, evidence=[setting_source(why)]))
    return values, assumptions, a


def price(values: dict, params: fees.Parameters, year: int, archive: list[dict]) -> fees.FeeEstimate:
    return fees.estimate(fees.inputs_from_record(values, params, year), params, archive)


def compute(conn: Connection, envelope_id: int) -> dict:
    env = conn.execute(text("SELECT received_at, title FROM intake.envelope WHERE envelope_id = :e"),
                       dict(e=envelope_id)).one()
    values, assumptions, a = pricing_values(conn, envelope_id)
    params = fees.Parameters(conn)
    received = env.received_at or datetime.now(timezone.utc)
    archive = fees.load_archive(conn, settings.dataset_version, before=received.date())
    labels = dict(conn.execute(text("SELECT code, label FROM ref.service UNION ALL SELECT code, label FROM ref.stage "
                                    "UNION ALL SELECT code, label FROM ref.typology")).all())
    base = dict(envelope_id=envelope_id, recommendation=a["recommendation"], assumptions=assumptions,
                rules=[r for r in a["rules"] if r["outcome"] != "flag"], flags=[r for r in a["rules"] if r["outcome"] == "flag"])
    try:
        est = price(values, params, received.year, archive)
    except fees.MissingInputs as e:
        names = {n: f.label for n, f in schema_fields().items()} | {"location_tier": "Location tier"}
        return base | dict(status="missing", missing=[{"field": f, "label": names.get(f, f)} for f in e.fields])
    except fees.PricingError as e:
        return base | dict(status="error", message=str(e))
    central = est.fee_total_local

    swings = []
    for asm in assumptions:
        x = asm.get("estimate") or {}
        if asm["field"] not in NUMERIC or x.get("low") is None or x["low"] == x["high"]:
            continue
        totals = []
        for v in (x["low"], x["high"]):
            try:
                totals.append(price(values | {asm["field"]: v}, params, received.year, archive).fee_total_local)
            except (fees.MissingInputs, fees.PricingError):
                totals.append(central)
        down, up = max(0.0, central - min(totals)), max(0.0, max(totals) - central)
        swings.append(dict(field=asm["field"], label=asm["label"], down=round(down), up=round(up)))
        asm["swing"] = {"down": round(down), "up": round(up)}

    won = est.range
    in_lo, in_hi = (won["low_local"], won["high_local"]) if won else (central, central)
    out_lo = central - math.sqrt((central - in_lo) ** 2 + sum(s["down"] ** 2 for s in swings))
    out_hi = central + math.sqrt((in_hi - central) ** 2 + sum(s["up"] ** 2 for s in swings))

    stage_options = []
    stage_assumed = any(asm["field"] == "stage_package" for asm in assumptions)
    if stage_assumed:
        priced = est.inputs.stages
        top = max((STAGE_LADDER.index(s) for s in priced if s in STAGE_LADDER), default=-1)
        for nxt in STAGE_LADDER[top + 1: top + 2]:
            try:
                more = price(values | {"stage_package": [*priced, nxt]}, params, received.year, archive)
                stage_options.append(dict(stage=nxt, label=labels.get(nxt, nxt),
                                          adds=round(more.fee_total_local - central)))
            except (fees.MissingInputs, fees.PricingError):
                pass

    by_stage: dict[str, float] = defaultdict(float)
    for ln in est.lines:
        by_stage[ln.stage] += ln.amount_local
    seq = params.stage_seq
    stages = [dict(stage=s, label=labels.get(s, s), amount=round(v)) for s, v in sorted(by_stage.items(),
                                                                                        key=lambda t: seq.get(t[0], 99))]
    p = est.inputs
    scope = " · ".join(x for x in (labels.get(p.typology, p.typology or ""), values.get("city") or "",
                                   ", ".join(labels.get(s, s) for s in p.services)) if x)
    why = [dict(label=FACTOR_LABELS.get(f.name, f.name.replace("_", " ").capitalize()), value=f.value, source=f.source)
           for f in est.factors]
    checks = fee_checks(assumptions, base["flags"], won)
    why += [dict(label="Assumed by the engine", value=n, source="fee method defaults") for n in p.assumed]
    return base | dict(
        status="priced", currency=est.currency, opening=round(central), opening_usd=round(est.fee_total_usd),
        won=won and dict(low=won["low_local"], high=won["high_local"], basis=won["basis"], cases=won["cases"]),
        band=dict(low=fees.round_sig(max(0.0, out_lo), 3), high=fees.round_sig(out_hi, 3)),
        swings=swings, stage_options=stage_options, stages=stages, scope=scope,
        stage_line=", ".join(labels.get(s, s) for s in p.stages),
        lines=[dict(service=labels.get(ln.service, ln.service), stage=labels.get(ln.stage, ln.stage),
                    amount=round(ln.amount_local), basis_qty=ln.basis_qty, basis_unit=ln.basis_unit,
                    factors=[dict(name=f.name, value=f.value, source=f.source) for f in ln.factors])
               for ln in est.lines],
        why=why, checks=checks, warnings=est.warnings, comparables=comparables(conn, envelope_id, limit=3),
        title=values.get("project_name") or env.title)


FLAG_CHECKS = {  # gate 1 flags that are worth checking before the fee goes out; facts (repeat client) are not
    "uncapped_liability": "check", "tight_deadline": "info", "cross_entity": "info",
}


def fee_checks(assumptions: list[dict], flags: list[dict], won: dict | None) -> list[dict]:
    """Things to check before the fee goes out: what was assumed, where the comparables are thin, and the client's
    terms. One sentence each, saying what to check."""
    out = []
    for a in assumptions:
        x = a.get("estimate") or {}
        if a["field"] in NUMERIC:
            where = (x.get("scope") or {}).get("level")
            n = len(x.get("comparables") or [])
            out.append(dict(level="check", evidence=a["evidence"],
                            text=f"{a['label']} assumed at {a['display']} from {n} project{'s' * (n != 1)} "
                                 f"({x.get('confidence', 'low')} confidence); confirm it with the client."))
            if where == "all":
                out.append(dict(level="info", evidence=a["evidence"],
                                text=f"Few comparable projects in this market: the {a['label'].lower()} estimate "
                                     "uses projects from all markets."))
        elif a["field"] in ("stage_package", "services"):
            out.append(dict(level="check", evidence=a["evidence"],
                            text=f"{a['label']} not stated; priced for {a['display']} by default. Confirm the scope."))
    for f in flags:
        level = FLAG_CHECKS.get(f["name"])
        if level:
            out.append(dict(level=level, text=f["why"], evidence=f["evidence"]))
    if not won:
        out.append(dict(level="info", evidence=[], text="No won bids of this kind to compare the fee with yet."))
    seen, unique = set(), []
    for c in out:
        if c["text"] not in seen:
            seen.add(c["text"])
            unique.append(c)
    return unique


FACTOR_LABELS = {"construction_cost_usd_m2": "Construction cost (USD per m²)", "modifiers": "Adjustments",
                 "market_band": "Market band"}


def view(conn: Connection, envelope_id: int) -> dict:
    card = compute(conn, envelope_id)
    decision = conn.execute(text("""SELECT decision, amount, engine_amount, currency, reason, note, decided_by,
                                           decided_at, proposal_id
                                    FROM intake.gate_decision WHERE envelope_id = :e AND gate = 2
                                    ORDER BY decided_at DESC, decision_id DESC LIMIT 1"""), dict(e=envelope_id)).first()
    card["decision"] = dict(decision._mapping) if decision else None
    card["proposal"] = None
    if decision and decision.proposal_id:
        v = conn.execute(text("""SELECT b.proposal_id, b.reference, v.version_no, v.status, v.fee_total_local
                                 FROM proposal.bid b JOIN proposal.version v USING (proposal_id)
                                 WHERE b.proposal_id = :p ORDER BY v.version_no DESC LIMIT 1"""),
                         dict(p=decision.proposal_id)).first()
        if v:
            vid = conn.execute(text("SELECT version_id FROM proposal.version WHERE proposal_id = :p AND version_no = :n"),
                               dict(p=v.proposal_id, n=v.version_no)).scalar()
            card["proposal"] = dict(v._mapping) | {"outputs": proposals.outputs(conn, vid)}
    return card


@router.get("/envelopes/{envelope_id}/fee")
def fee_card(envelope_id: int, _: Principal = READ):
    with engine.connect() as conn:
        return view(conn, envelope_id)


class FeeDecision(BaseModel):
    decision: str = PField(pattern="^(approve|override|reject)$")
    amount: float | None = PField(None, gt=0)
    reason: str | None = PField(None, max_length=500)
    note: str | None = PField(None, max_length=2000)


def scale_lines(conn: Connection, proposal_id: int, amount: float, reason: str, who: Principal) -> None:
    """Set the proposal's total to the director's figure by scaling every fee line; the engine's amounts stay."""
    v = conn.execute(text("SELECT version_id, fee_total_local FROM proposal.version WHERE proposal_id = :p "
                          "AND version_no = 1"), dict(p=proposal_id)).one()
    lines = conn.execute(text("SELECT line_no, amount_local FROM proposal.line WHERE version_id = :v ORDER BY line_no"),
                         dict(v=v.version_id)).all()
    total = float(sum(a for _, a in lines))
    new = [round(float(a) * amount / total) for _, a in lines]
    new[-1] += round(amount) - sum(new)
    for (line_no, _), a in zip(lines, new):
        conn.execute(text("UPDATE proposal.line SET amount_local = :a, note = :note WHERE version_id = :v AND line_no = :n"),
                     dict(a=a, v=v.version_id, n=line_no, note=f"Director's figure: {reason}"))
    proposals.recompute(conn, v.version_id)
    proposals.bump(conn, v.version_id)
    proposals.log(conn, proposal_id, v.version_id, "override", who, "fee total",
                  {"fee_total_local": float(v.fee_total_local)}, {"fee_total_local": round(amount)}, reason)


@router.post("/envelopes/{envelope_id}/fee/decision")
def decide(envelope_id: int, body: FeeDecision, who: Principal = APPROVE):
    """Approve, override or reject the fee. The system suggests a figure; a director decides it."""
    if body.decision in ("override", "reject") and not (body.reason or "").strip():
        raise HTTPException(422, "give a reason")
    if body.decision == "override" and not body.amount:
        raise HTTPException(422, "give the amount")
    with engine.begin() as conn:
        card = compute(conn, envelope_id)
        if card["status"] != "priced":
            raise HTTPException(409, "the fee cannot be priced yet: " + ", ".join(m["label"] for m in card.get("missing", [])))
        last = conn.execute(text("""SELECT decision FROM intake.gate_decision WHERE envelope_id = :e AND gate = 2
                                    ORDER BY decided_at DESC, decision_id DESC LIMIT 1"""), dict(e=envelope_id)).scalar()
        if last in ("approve", "override") and body.decision != "reject":
            raise HTTPException(409, "already approved; the proposal holds the figure now")
        proposal_id = None
        if body.decision != "reject":
            assumed = [dict(field=a["field"], value=a["display"], method=(a.get("estimate") or {}).get("method"))
                       for a in card["assumptions"]]
            extra = {k: v for k, v in pricing_values(conn, envelope_id)[0].items() if k in (
                "gfa_m2", "site_area_m2", "fitout_area_m2", "landscape_area_m2", "services", "stage_package",
                "billing_currency", "location_tier", "typology", "country", "city")}
            record.confirm(conn, envelope_id, who.email, body.reason or "Fee approved on the fee card",
                           extra | {"_assumptions": assumed, "_fee_card": {"opening": card["opening"], "band": card["band"]}})
            proposal_id = proposals.create(conn, envelope_id, {}, who)
            if body.decision == "override":
                scale_lines(conn, proposal_id, body.amount, body.reason, who)
        conn.execute(text("""INSERT INTO intake.gate_decision (envelope_id, gate, decision, recommendation, reason, note,
                               decided_by, amount, engine_amount, currency, proposal_id)
                             VALUES (:e, 2, :d, :rec, :reason, :note, :who, :amount, :engine, :cur, :p)"""),
                     dict(e=envelope_id, d=body.decision, rec=card["recommendation"], reason=body.reason, note=body.note,
                          who=who.email, amount=body.amount if body.decision == "override" else card["opening"],
                          engine=card["opening"], cur=card["currency"], p=proposal_id))
    with engine.connect() as conn:
        return view(conn, envelope_id)


def latest_proposal(conn: Connection, envelope_id: int) -> tuple[int, int] | None:
    """The proposal the approved fee created, and its latest version."""
    p = conn.execute(text("""SELECT proposal_id FROM intake.gate_decision WHERE envelope_id = :e AND gate = 2
                             AND proposal_id IS NOT NULL ORDER BY decided_at DESC, decision_id DESC LIMIT 1"""),
                     dict(e=envelope_id)).scalar()
    if p is None:
        return None
    n = conn.execute(text("SELECT max(version_no) FROM proposal.version WHERE proposal_id = :p"), dict(p=p)).scalar()
    return p, n


def proposal_view(conn: Connection, envelope_id: int) -> dict:
    found = latest_proposal(conn, envelope_id)
    v = proposals.load_version(conn, *found) if found else None
    if v:
        tids = {k: export.template_id(path.read_bytes(), k) for k, path in export.TEMPLATES.items()}
        v["outputs_by_template"] = {k: [o for o in v["outputs"] if o["template"] == tid] for k, tid in tids.items()}
        v["pending"] = [s["code"] for s in v["sections"] if s["origin"] == "pending"]
    return {"proposal": v, "templates": [{"key": k, "name": n} for k, n in export.TEMPLATE_NAMES.items()]}


@router.get("/envelopes/{envelope_id}/proposal")
def proposal(envelope_id: int, _: Principal = READ):
    with engine.connect() as conn:
        return proposal_view(conn, envelope_id)


@router.post("/envelopes/{envelope_id}/proposal/draft")
def draft(envelope_id: int, who: Principal = APPROVE):
    """Write the proposal's prose: Claude drafts the understanding and client-specific scope and terms, cited,
    on top of the practice's standard text. One model call."""
    with engine.connect() as conn:
        found = latest_proposal(conn, envelope_id)
        if not found:
            raise HTTPException(409, "approve the fee first")
        v = conn.execute(text("SELECT revision, status FROM proposal.version WHERE proposal_id = :p AND version_no = :n"),
                         dict(p=found[0], n=found[1])).one()
    if v.status == "draft":
        drafting.draft(found[0], found[1], drafting.DraftRequest(revision=v.revision), who)
    with engine.connect() as conn:
        return proposal_view(conn, envelope_id)


class ExportRequest(BaseModel):
    template: str = PField("full", pattern="^(full|letter)$")


@router.post("/envelopes/{envelope_id}/proposal/export")
def export_proposal(envelope_id: int, body: ExportRequest, who: Principal = APPROVE):
    """Approve the proposal as it stands (gate 3) and export it as the chosen template, Word and PDF. If it
    cannot be approved yet, say what is left to do."""
    with engine.connect() as conn:
        found = latest_proposal(conn, envelope_id)
        if not found:
            raise HTTPException(409, "approve the fee first")
        v = conn.execute(text("SELECT revision, status FROM proposal.version WHERE proposal_id = :p AND version_no = :n"),
                         dict(p=found[0], n=found[1])).one()
    if v.status == "draft":
        try:
            proposals.approve(found[0], found[1], proposals.Edit(revision=v.revision, reason="Approved for export"), who)
        except HTTPException as e:
            if e.status_code == 409 and isinstance(e.detail, dict):
                raise HTTPException(409, {"message": "the proposal is not ready to send yet",
                                          "problems": e.detail.get("problems", [])})
            raise
    export.export(found[0], found[1], who, body.template)
    with engine.connect() as conn:
        return proposal_view(conn, envelope_id)
