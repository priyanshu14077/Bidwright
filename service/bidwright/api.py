"""HTTP API for the Bidwright app and for integrations (n8n, mail ingest).

Every endpoint asks for one permission (see tenancy.PERMISSIONS) and runs inside
the caller's workspace; row-level security does the rest.

    uv run uvicorn bidwright.api:app --reload --port 8000
"""

import json
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field as PField
from sqlalchemy import text

from bidwright import chat, drafting, export, feecard, fees, gate1, proposals, record
from bidwright.auth import require, router as auth_router
from bidwright.config import settings
from bidwright.db import engine, in_workspace
from bidwright.engine import comparables, run_engine
from bidwright.extraction import extract_envelope
from bidwright.intake.ingest import ingest
from bidwright.schema import SCHEMA_VERSION, fields
from bidwright.storage import blob_store
from bidwright.tenancy import Principal

app = FastAPI(title="Bidwright", version="0.2.0")
app.include_router(auth_router)
app.include_router(proposals.router)
app.include_router(drafting.router)
app.include_router(export.router)
app.include_router(gate1.router)
app.include_router(feecard.router)
app.include_router(chat.router)

READ, INTAKE, REVIEW, REFERENCE, BACKTEST = (Depends(require(p)) for p in
                                             ("read", "intake", "review", "reference", "backtest"))


def rows(sql: str, **params) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


# ---------------------------------------------------------------- intake

@app.post("/api/envelopes")
async def upload(background: BackgroundTasks, files: list[UploadFile] = File(...), channel: str = Form("upload"),
                 sender: str | None = Form(None), who: Principal = INTAKE):
    payload = [(f.filename or "file", await f.read()) for f in files]
    with engine.begin() as conn:
        envelope_id, created = ingest(conn, payload, channel, sender=sender)
        if created:
            conn.execute(text("UPDATE intake.envelope SET status = 'extracting' WHERE envelope_id = :e"),
                         dict(e=envelope_id))
    if created:
        background.add_task(in_workspace, who.org_id, extract_envelope, envelope_id)
    return {"envelope_id": envelope_id, "created": created}


TEXT_SOURCES = {"whatsapp": "WhatsApp message", "email": "Email", "notes": "Notes", "upload": "Pasted text"}


class PastedText(BaseModel):
    text: str = PField(min_length=1, max_length=200_000)
    channel: Literal["whatsapp", "email", "notes", "upload"] = "upload"
    sender: str | None = PField(None, max_length=200)
    received_at: datetime | None = None


def text_source(body: PastedText) -> tuple[datetime, str, bytes]:
    at = body.received_at or datetime.now(timezone.utc)
    who = f" from {re.sub(r'[^\w .+-]', '', body.sender)[:60]}" if body.sender else ""
    return at, f"{TEXT_SOURCES[body.channel]}{who} {at:%Y-%m-%d %H.%M}.txt", body.text.replace("\r\n", "\n").encode()


@app.post("/api/envelopes/text")
def paste(body: PastedText, background: BackgroundTasks, who: Principal = INTAKE):
    """A request pasted or forwarded as text: a WhatsApp message or thread, an email, call notes."""
    at, name, data = text_source(body)
    with engine.begin() as conn:
        envelope_id, created = ingest(conn, [(name, data)], body.channel, sender=body.sender,
                                      title=name.removesuffix(".txt"), received_at=at)
        if created:
            conn.execute(text("UPDATE intake.envelope SET status = 'extracting' WHERE envelope_id = :e"),
                         dict(e=envelope_id))
    if created:
        background.add_task(in_workspace, who.org_id, extract_envelope, envelope_id)
    return {"envelope_id": envelope_id, "created": created}


@app.post("/api/envelopes/{envelope_id}/text")
def add_text(envelope_id: int, body: PastedText, background: BackgroundTasks, who: Principal = INTAKE):
    """The client's reply (or any later message) added to a request; the request is read again with it."""
    at, name, data = text_source(body)
    with engine.begin() as conn:
        if not conn.execute(text("SELECT 1 FROM intake.envelope WHERE envelope_id = :e"), dict(e=envelope_id)).first():
            raise HTTPException(404, "no such request")
        ingest(conn, [(name, data)], body.channel, sender=body.sender, received_at=at, envelope_id=envelope_id)
        conn.execute(text("UPDATE intake.envelope SET status = 'extracting' WHERE envelope_id = :e"), dict(e=envelope_id))
    background.add_task(in_workspace, who.org_id, extract_envelope, envelope_id)
    return {"envelope_id": envelope_id, "status": "extracting"}


@app.get("/api/documents/{document_id}/text")
def document_text(document_id: int, _: Principal = READ):
    """A text source (message, notes, email, transcribed screenshot) as text, for showing quotes in place."""
    with engine.connect() as conn:
        uri = conn.execute(text("SELECT text_uri FROM lineage.source_document WHERE document_id = :d"),
                           dict(d=document_id)).first()
    if not uri or not uri.text_uri:
        raise HTTPException(404, "this document has no text form")
    return Response(blob_store.get(uri.text_uri), media_type="text/plain; charset=utf-8")


@app.post("/api/envelopes/{envelope_id}/extract")
def rerun(envelope_id: int, background: BackgroundTasks, who: Principal = INTAKE):
    background.add_task(in_workspace, who.org_id, extract_envelope, envelope_id)
    return {"envelope_id": envelope_id, "status": "extracting"}


# ---------------------------------------------------------------- inbox and review

@app.get("/api/envelopes")
def inbox(_: Principal = READ):
    return rows("""
        SELECT e.envelope_id, e.title, e.channel, e.sender, e.received_at, e.submission_deadline, e.status,
               e.lead_status, e.completeness, e.owner, e.country, l.city, l.tier, e.billing_currency, e.routed_studio,
               (SELECT normalized->>'value' FROM lineage.field_value fv WHERE fv.envelope_id = e.envelope_id
                  AND fv.field = 'project_name' AND fv.status IN ('proposed','confirmed') ORDER BY (origin='human') DESC,
                  field_value_id LIMIT 1) AS project_name,
               (SELECT count(*) FROM intake.field_conflict c WHERE c.envelope_id = e.envelope_id AND c.status = 'open') AS open_conflicts,
               (SELECT count(*) FROM intake.engine_flag f WHERE f.envelope_id = e.envelope_id AND f.severity <> 'info') AS flags,
               (SELECT count(*) FROM lineage.source_document d WHERE d.envelope_id = e.envelope_id) AS documents,
               (SELECT max(version) FROM intake.opportunity o WHERE o.envelope_id = e.envelope_id) AS confirmed_version,
               (SELECT decision FROM intake.gate_decision d WHERE d.envelope_id = e.envelope_id AND d.gate = 1
                  ORDER BY decided_at DESC, decision_id DESC LIMIT 1) AS gate1_decision,
               (SELECT decision FROM intake.gate_decision d WHERE d.envelope_id = e.envelope_id AND d.gate = 2
                  ORDER BY decided_at DESC, decision_id DESC LIMIT 1) AS gate2_decision,
               (SELECT jsonb_array_length(assessment->'ask') FROM intake.assessment a
                  WHERE a.envelope_id = e.envelope_id) AS open_questions,
               p.proposal_id, p.reference AS proposal_reference, p.version_no AS proposal_version,
               p.status AS proposal_status, p.fee_total_local AS proposal_total, p.currency AS proposal_currency
        FROM intake.envelope e LEFT JOIN ref.location l USING (location_id)
        LEFT JOIN LATERAL (SELECT b.proposal_id, b.reference, v.version_no, v.status, v.fee_total_local, v.currency
                           FROM proposal.bid b JOIN proposal.version v USING (proposal_id)
                           WHERE b.envelope_id = e.envelope_id
                           ORDER BY b.created_at DESC, v.version_no DESC LIMIT 1) p ON true
        ORDER BY e.received_at DESC""")


@app.get("/api/envelopes/{envelope_id}")
def envelope(envelope_id: int, _: Principal = READ):
    env = rows("""SELECT e.*, l.city, l.tier FROM intake.envelope e LEFT JOIN ref.location l USING (location_id)
                  WHERE envelope_id = :e""", e=envelope_id)
    if not env:
        raise HTTPException(404)
    docs = rows("""SELECT d.document_id, d.file_name, d.doc_class, d.page_count, d.supported, d.mime_type, d.size_bytes,
                          d.channel, d.parent_document_id, d.render_uri IS NOT NULL AS has_pdf,
                          COALESCE((SELECT json_agg(json_build_object('page', page_no, 'width', width, 'height', height)
                                                    ORDER BY page_no) FROM lineage.page p
                                    WHERE p.document_id = d.document_id), '[]') AS pages
                   FROM lineage.source_document d WHERE envelope_id = :e ORDER BY document_id""", e=envelope_id)
    with engine.connect() as conn:
        rec = record.build(conn, envelope_id)
        comps = comparables(conn, envelope_id) if env[0]["status"] in ("review", "confirmed") else None
    return {"envelope": env[0], "documents": docs, "record": rec, "comparables": comps,
            "schema_version": SCHEMA_VERSION}


@app.get("/api/documents/{document_id}/pdf")
def document_pdf(document_id: int, _: Principal = READ):
    uri = rows("SELECT render_uri FROM lineage.source_document WHERE document_id = :d", d=document_id)
    if not uri or not uri[0]["render_uri"]:
        raise HTTPException(404)
    return Response(blob_store.get(uri[0]["render_uri"]), media_type="application/pdf")


@app.get("/api/documents/{document_id}/original")
def document_original(document_id: int, _: Principal = READ):
    doc = rows("SELECT blob_uri, file_name, mime_type FROM lineage.source_document WHERE document_id = :d", d=document_id)
    if not doc:
        raise HTTPException(404)
    return Response(blob_store.get(doc[0]["blob_uri"]), media_type=doc[0]["mime_type"] or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{doc[0]["file_name"]}"'})


# ---------------------------------------------------------------- changes

class Change(BaseModel):
    action: str = PField(pattern="^(edit|accept_suggestion|resolve_conflict|accept)$")
    reason: str = PField(min_length=3)
    value: object = None
    field_value_id: int | None = None


@app.post("/api/envelopes/{envelope_id}/fields/{field_name}")
def change_field(envelope_id: int, field_name: str, change: Change, who: Principal = REVIEW):
    if field_name not in fields() and field_name not in record.ENGINE_FIELDS:
        raise HTTPException(400, f"unknown field {field_name}")
    with engine.begin() as conn:
        status = conn.execute(text("SELECT status FROM intake.envelope WHERE envelope_id = :e"), dict(e=envelope_id)).scalar()
        if status == "confirmed":
            raise HTTPException(409, "record is confirmed and locked; re-open it as a new version first")
        current = record.build(conn, envelope_id)[field_name]
        old_value = current["value"]
        if change.action in ("resolve_conflict", "accept"):
            chosen = conn.execute(text("SELECT normalized, value FROM lineage.field_value WHERE field_value_id = :i "
                                       "AND envelope_id = :e"), dict(i=change.field_value_id, e=envelope_id)).one()
            new_value, written = chosen.normalized["value"], chosen.value.get("value_text")
        elif change.action == "accept_suggestion":
            new_value, written = current["suggestion"], None
            if new_value is None:
                raise HTTPException(400, "no suggestion to accept")
        else:
            new_value, written = change.value, None
        conn.execute(text("""UPDATE lineage.field_value SET status = 'superseded'
                             WHERE envelope_id = :e AND field = :f AND origin = 'human' AND status = 'proposed'"""),
                     dict(e=envelope_id, f=field_name))
        new_id = conn.execute(
            text("""INSERT INTO lineage.field_value (envelope_id, field, value, normalized, document_id, page, quote, bbox,
                    confidence, origin, kind, status)
                    SELECT :e, :f, CAST(:v AS jsonb), CAST(:n AS jsonb), src.document_id, src.page, src.quote, src.bbox,
                           1.0, 'human', 'fact', 'proposed'
                    FROM (SELECT 1) one LEFT JOIN lineage.field_value src ON src.field_value_id = :src
                    RETURNING field_value_id"""),
            dict(e=envelope_id, f=field_name, src=change.field_value_id,
                 v=json.dumps({"value_text": written or json.dumps(new_value), "reason": change.reason,
                               "based_on": change.field_value_id}),
                 n=json.dumps({"value": new_value, "notes": [f"set by {who.email}"]})),
        ).scalar_one()
        conn.execute(
            text("""INSERT INTO lineage.field_change (field, old_field_value_id, new_field_value_id, old_value, new_value,
                    action, reason, changed_by) VALUES (:f, :old, :new, CAST(:ov AS jsonb), CAST(:nv AS jsonb), :a, :r, :u)"""),
            dict(f=field_name, old=(current["sources"] or [{}])[0].get("field_value_id") if current["sources"] else None,
                 new=new_id, ov=json.dumps(old_value), nv=json.dumps(new_value),
                 a="resolve_conflict" if change.action == "resolve_conflict" else
                   ("accept" if change.action in ("accept", "accept_suggestion") else "edit"),
                 r=change.reason, u=who.email))
        if change.action == "resolve_conflict":
            conn.execute(text("""UPDATE intake.field_conflict SET status = 'resolved', resolved_value_id = :v
                                 WHERE envelope_id = :e AND field = :f AND status = 'open'"""),
                         dict(v=change.field_value_id, e=envelope_id, f=field_name))
    run_engine(envelope_id)
    return {"field_value_id": new_id}


class Confirm(BaseModel):
    reason: str = "Reviewed against source documents"


@app.post("/api/envelopes/{envelope_id}/confirm")
def confirm(envelope_id: int, body: Confirm, who: Principal = REVIEW):
    with engine.begin() as conn:
        open_conflicts = conn.execute(text("SELECT count(*) FROM intake.field_conflict WHERE envelope_id = :e "
                                           "AND status = 'open'"), dict(e=envelope_id)).scalar()
        if open_conflicts:
            raise HTTPException(409, f"{open_conflicts} open conflict(s) must be resolved before confirming")
        rec = record.build(conn, envelope_id)
        snapshot = {k: v["value"] for k, v in rec.items() if not k.startswith("_")}
        env = conn.execute(text("SELECT lead_status, completeness, country, billing_currency, routed_studio "
                                "FROM intake.envelope WHERE envelope_id = :e"), dict(e=envelope_id)).one()
        snapshot |= {"lead_status": env.lead_status, "completeness": float(env.completeness or 0)}
        version = (conn.execute(text("SELECT max(version) FROM intake.opportunity WHERE envelope_id = :e"),
                                dict(e=envelope_id)).scalar() or 0) + 1
        conn.execute(text("INSERT INTO intake.opportunity (envelope_id, version, record, schema_version, confirmed_by) "
                          "VALUES (:e, :v, CAST(:r AS jsonb), :sv, :u)"),
                     dict(e=envelope_id, v=version, r=json.dumps(snapshot, default=str), sv=SCHEMA_VERSION, u=who.email))
        ids = conn.execute(text("""UPDATE lineage.field_value SET status = 'confirmed' WHERE envelope_id = :e
                                   AND status = 'proposed' RETURNING field_value_id"""), dict(e=envelope_id)).scalars().all()
        conn.execute(text("""INSERT INTO lineage.field_change (field, new_value, action, reason, changed_by)
                             VALUES ('*', CAST(:v AS jsonb), 'confirm', :r, :u)"""),
                     dict(v=json.dumps({"envelope_id": envelope_id, "version": version, "values_locked": len(ids)}),
                          r=body.reason, u=who.email))
        conn.execute(text("UPDATE intake.envelope SET status = 'confirmed', updated_at = now() WHERE envelope_id = :e"),
                     dict(e=envelope_id))
    return {"envelope_id": envelope_id, "version": version}


# ---------------------------------------------------------------- fees

@app.get("/api/envelopes/{envelope_id}/fees")
def fee_estimate(envelope_id: int, complexity: str | None = None, cost_type: str | None = None,
                 construction_cost_usd_m2: float | None = None, _: Principal = READ):
    """A fee estimate for the latest confirmed record. The query parameters are the estimator's pricing choices."""
    choices = proposals.PricingChoices(complexity=complexity, cost_type=cost_type,
                                       construction_cost_usd_m2=construction_cost_usd_m2).choices()
    with engine.connect() as conn:
        opp, est, _ = proposals.price(conn, envelope_id, choices)
    return {"envelope_id": envelope_id, "record_version": opp.version, **est.to_dict()}


# ---------------------------------------------------------------- pricing settings

@app.get("/api/settings/pricing")
def pricing_settings(_: Principal = READ):
    """Everything the fee engine reads for this workspace, and what is still missing."""
    with engine.connect() as conn:
        ready = fees.readiness(conn)
    return {
        "readiness": ready,
        "fee_method": rows("SELECT key, value_text, value, unit, rationale FROM ref.fee_assumption ORDER BY key"),
        "cost_ratios": rows("SELECT cost_type, ratio FROM ref.typology_cost_ratio ORDER BY cost_type"),
        "markets": rows("""SELECT m.*, c.name AS country_name, c.national_currency FROM ref.market m
                           JOIN ref.country c ON c.code = m.country ORDER BY c.region, c.name"""),
        "construction_costs": rows("""SELECT cc.*, c.name AS country_name FROM ref.construction_cost cc
                                      JOIN ref.country c ON c.code = cc.country
                                      ORDER BY c.region, c.name, cc.city, cc.cost_type"""),
        "studios": rows("SELECT * FROM ref.studio ORDER BY code"),
        "changes": rows("SELECT * FROM ref.setting_change ORDER BY changed_at DESC LIMIT 100"),
    }


def log_setting_change(conn, setting: str, key: str, old: dict | None, new: dict, reason: str | None,
                       who: Principal) -> None:
    conn.execute(text("""INSERT INTO ref.setting_change (setting, key, old, new, reason, changed_by)
                         VALUES (:s, :k, CAST(:old AS jsonb), CAST(:new AS jsonb), :r, :u)"""),
                 dict(s=setting, k=key, old=json.dumps(old, default=str) if old else None,
                      new=json.dumps(new, default=str), r=reason, u=who.email))


class CostFigure(BaseModel):
    country: str = PField(min_length=2, max_length=2)
    city: str = ""  # empty: the country-wide figure
    cost_type: str
    usd_m2: float = PField(gt=0)
    price_year: int | None = None  # defaults to this year
    note: str | None = None
    reason: str | None = None


@app.put("/api/settings/pricing/construction-costs")
def set_construction_cost(body: CostFigure, who: Principal = REFERENCE):
    if body.cost_type not in fees.COST_TYPES:
        raise HTTPException(422, f"cost_type must be one of {', '.join(fees.COST_TYPES)}")
    key = dict(country=body.country.upper(), city=body.city.strip(), cost_type=body.cost_type)
    with engine.begin() as conn:
        if not conn.execute(text("SELECT 1 FROM ref.country WHERE code = :c"), dict(c=key["country"])).first():
            raise HTTPException(422, f"unknown country {key['country']}")
        old = conn.execute(text("""SELECT usd_m2, price_year, basis, source, note FROM ref.construction_cost
                                   WHERE country = :country AND city = :city AND cost_type = :cost_type"""),
                           key).mappings().first()
        new = dict(key, usd_m2=body.usd_m2, price_year=body.price_year or datetime.now().year, basis="estimator",
                   source=f"entered by {who.email}", note=body.note)
        conn.execute(text("""INSERT INTO ref.construction_cost
                               (country, city, cost_type, usd_m2, price_year, basis, source, note, updated_by)
                             VALUES (:country, :city, :cost_type, :usd_m2, :price_year, :basis, :source, :note, :by)
                             ON CONFLICT (org_id, country, city, cost_type) DO UPDATE SET usd_m2 = EXCLUDED.usd_m2,
                               price_year = EXCLUDED.price_year, basis = EXCLUDED.basis, source = EXCLUDED.source,
                               note = EXCLUDED.note, updated_by = EXCLUDED.updated_by, updated_at = now()"""),
                     dict(new, by=who.email))
        log_setting_change(conn, "construction_cost", f"{key['country']}/{key['city'] or '*'}/{body.cost_type}",
                           dict(old) if old else None, new, body.reason, who)
    return new


class MarketSettings(BaseModel):
    band: str | None = None
    fee_factor: float | None = PField(None, gt=0)
    escalation_pct: float | None = None
    reason: str | None = None


@app.put("/api/settings/pricing/markets/{country}")
def set_market(country: str, body: MarketSettings, who: Principal = REFERENCE):
    country = country.upper()
    if body.band not in (None, "high", "low"):
        raise HTTPException(422, "band must be high or low")
    changes = {k: v for k, v in body.model_dump(exclude={"reason"}).items() if v is not None}
    with engine.begin() as conn:
        old = conn.execute(text("SELECT band, fee_factor, escalation_pct FROM ref.market WHERE country = :c"),
                           dict(c=country)).mappings().first()
        if old is None:
            if set(changes) != {"band", "fee_factor", "escalation_pct"}:
                raise HTTPException(422, "a new market needs band, fee_factor and escalation_pct")
            if not conn.execute(text("SELECT 1 FROM ref.country WHERE code = :c"), dict(c=country)).first():
                raise HTTPException(422, f"unknown country {country}")
            conn.execute(text("""INSERT INTO ref.market (country, band, fee_factor, fee_factor_source, escalation_pct,
                                   escalation_source, updated_by)
                                 VALUES (:c, :band, :fee_factor, :src, :escalation_pct, :src, :by)"""),
                         dict(changes, c=country, src=f"set by {who.email}", by=who.email))
        elif changes:
            sets = ", ".join(f"{k} = :{k}" for k in changes)
            sources = ", fee_factor_source = :src" if "fee_factor" in changes else ""
            sources += ", escalation_source = :src" if "escalation_pct" in changes else ""
            conn.execute(text(f"UPDATE ref.market SET {sets}{sources}, updated_by = :by, updated_at = now() "
                              "WHERE country = :c"),
                         dict(changes, c=country, src=f"set by {who.email}", by=who.email))
        log_setting_change(conn, "market", country, dict(old) if old else None, changes, body.reason, who)
    return {"country": country, **changes}


class AssumptionValue(BaseModel):
    value: float | str
    reason: str | None = None


@app.put("/api/settings/pricing/fee-method/{key}")
def set_fee_assumption(key: str, body: AssumptionValue, who: Principal = REFERENCE):
    numeric = isinstance(body.value, (int, float))
    with engine.begin() as conn:
        old = conn.execute(text("SELECT value_text, value FROM ref.fee_assumption WHERE key = :k"),
                           dict(k=key)).mappings().first()
        if old is None:
            raise HTTPException(404, f"no fee method value {key}")
        if old["value"] is not None and not numeric:
            raise HTTPException(422, f"{key} is a number")
        conn.execute(text("""UPDATE ref.fee_assumption SET value_text = :t, value = :v, dataset_version = 'workspace'
                             WHERE key = :k"""), dict(k=key, t=str(body.value), v=body.value if numeric else None))
        log_setting_change(conn, "fee_assumption", key, dict(old), {"value": body.value}, body.reason, who)
    return {"key": key, "value": body.value}


# ---------------------------------------------------------------- standard text and studios

CLAUSE_KINDS = ("scope", "exclusion", "terms", "position")


@app.get("/api/settings/library")
def library(_: Principal = READ):
    return rows("SELECT * FROM ref.clause ORDER BY kind, seq, key")


class ClauseText(BaseModel):
    body: str = PField(min_length=1)
    label: str | None = None
    reason: str | None = None


@app.put("/api/settings/library/{kind}/{key}")
def set_clause(kind: str, key: str, body: ClauseText, who: Principal = REFERENCE):
    """Change a clause, or add one (a scope for a service and stage, an exclusion, a clause position)."""
    if kind not in CLAUSE_KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(CLAUSE_KINDS)}")
    service = stage = None
    if kind == "scope":
        service, _, stage = key.partition(".")
    with engine.begin() as conn:
        old = conn.execute(text("SELECT body, label FROM ref.clause WHERE kind = :k AND key = :key"),
                           dict(k=kind, key=key)).mappings().first()
        if old is None and kind == "terms":
            raise HTTPException(404, "terms has only the standard paragraph and the sign-off")
        seq = conn.execute(text("SELECT coalesce(max(seq), -1) + 1 FROM ref.clause WHERE kind = :k"),
                           dict(k=kind)).scalar()
        try:
            with conn.begin_nested():
                conn.execute(text("""INSERT INTO ref.clause (kind, key, service, stage, seq, label, body, updated_by)
                                     VALUES (:k, :key, :service, :stage, :seq, :label, :body, :who)
                                     ON CONFLICT (org_id, kind, key) DO UPDATE SET body = EXCLUDED.body,
                                       label = COALESCE(EXCLUDED.label, ref.clause.label),
                                       updated_by = EXCLUDED.updated_by, updated_at = now()"""),
                             dict(k=kind, key=key, service=service, stage=stage, seq=seq, label=body.label,
                                  body=body.body.strip(), who=who.email))
        except Exception:
            raise HTTPException(422, "a scope key is <service>.<stage>, both known codes")
        log_setting_change(conn, "clause", f"{kind}/{key}", dict(old) if old else None,
                           {"body": body.body.strip(), "label": body.label}, body.reason, who)
    return {"kind": kind, "key": key}


@app.delete("/api/settings/library/{kind}/{key}")
def delete_clause(kind: str, key: str, reason: str, who: Principal = REFERENCE):
    if kind == "terms":
        raise HTTPException(422, "the standard terms and sign-off can be changed, not removed")
    with engine.begin() as conn:
        old = conn.execute(text("DELETE FROM ref.clause WHERE kind = :k AND key = :key RETURNING body, label"),
                           dict(k=kind, key=key)).mappings().first()
        if old is None:
            raise HTTPException(404, "no such clause")
        log_setting_change(conn, "clause", f"{kind}/{key}", dict(old), {"deleted": True}, reason, who)
    return {"deleted": f"{kind}/{key}"}


class Studio(BaseModel):
    name: str = PField(min_length=2)
    city: str = PField(min_length=2)
    country: str = PField(min_length=2, max_length=2)
    reason: str | None = None


@app.put("/api/settings/studios/{code}")
def set_studio(code: str, body: Studio, who: Principal = REFERENCE):
    code = code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,6}", code):
        raise HTTPException(422, "a studio code is 2 to 6 letters or digits, for example DXB")
    with engine.begin() as conn:
        if not conn.execute(text("SELECT 1 FROM ref.country WHERE code = :c"), dict(c=body.country.upper())).first():
            raise HTTPException(422, f"unknown country {body.country}")
        old = conn.execute(text("SELECT name, city, country FROM ref.studio WHERE code = :c"),
                           dict(c=code)).mappings().first()
        new = dict(name=body.name.strip(), city=body.city.strip(), country=body.country.upper())
        conn.execute(text("""INSERT INTO ref.studio (code, name, city, country) VALUES (:code, :name, :city, :country)
                             ON CONFLICT (org_id, code) DO UPDATE SET name = EXCLUDED.name, city = EXCLUDED.city,
                               country = EXCLUDED.country"""), dict(new, code=code))
        log_setting_change(conn, "studio", code, dict(old) if old else None, new, body.reason, who)
    return {"code": code, **new}


# ---------------------------------------------------------------- audit, mapping, admin, observability

@app.get("/api/envelopes/{envelope_id}/audit")
def audit(envelope_id: int, _: Principal = READ):
    values = rows("""SELECT fv.field_value_id, fv.field, fv.value->>'value_text' AS value_text, fv.normalized->'value' AS normalized,
                            fv.origin, fv.kind, fv.status, fv.confidence, fv.created_at, d.file_name, fv.page,
                            r.model, r.prompt_version, r.engine_version, r.kind AS run_kind
                     FROM lineage.field_value fv LEFT JOIN lineage.source_document d USING (document_id)
                     LEFT JOIN intake.extraction_run r ON r.run_id = fv.run_id
                     WHERE fv.envelope_id = :e ORDER BY fv.field, fv.created_at, fv.field_value_id""", e=envelope_id)
    changes = rows("""SELECT c.* FROM lineage.field_change c WHERE c.new_field_value_id IN
                        (SELECT field_value_id FROM lineage.field_value WHERE envelope_id = :e)
                      OR (c.action = 'confirm' AND (c.new_value->>'envelope_id')::bigint = :e)
                      ORDER BY c.changed_at""", e=envelope_id)
    runs = rows("""SELECT r.run_id, r.kind, r.model, r.prompt_version, r.engine_version, r.schema_version, r.dataset_version,
                          r.status, r.trace_id, r.started_at, r.finished_at, d.file_name,
                          (SELECT count(*) FROM intake.llm_call c WHERE c.run_id = r.run_id) AS calls,
                          (SELECT sum(cost_usd) FROM intake.llm_call c WHERE c.run_id = r.run_id) AS cost_usd
                   FROM intake.extraction_run r LEFT JOIN lineage.source_document d USING (document_id)
                   WHERE r.envelope_id = :e ORDER BY r.run_id""", e=envelope_id)
    versions = rows("SELECT version, confirmed_by, confirmed_at, schema_version FROM intake.opportunity "
                    "WHERE envelope_id = :e ORDER BY version", e=envelope_id)
    return {"values": values, "changes": changes, "runs": runs, "versions": versions}


SECTION_MAP = [
    ("understanding", "Project understanding", ["project_name", "client_name", "client_status", "country", "city",
                                                 "location_tier", "typology", "gfa_m2", "site_area_m2", "units", "keys"]),
    ("scope", "Scope of services", ["services", "stage_package", "fitout_area_m2", "landscape_area_m2"]),
    ("programme", "Programme", ["issue_date", "submission_deadline"]),
    ("fees", "Fee proposal", ["billing_currency", "routed_studio"]),
    ("payment", "Payment schedule", ["payment_terms_days"]),
    ("exclusions", "Exclusions", ["excluded_scope"]),
    ("terms", "Terms and conditions", ["liability_cap"]),
]


@app.get("/api/envelopes/{envelope_id}/mapping")
def mapping(envelope_id: int, _: Principal = READ):
    with engine.connect() as conn:
        rec = record.build(conn, envelope_id)
        usual = {code: n for code, n in conn.execute(text(
            "SELECT section_code, count(DISTINCT proposal_id) FROM archive.proposal_section GROUP BY 1"))}
        total = conn.execute(text("SELECT count(*) FROM archive.proposal WHERE data_origin <> 'rfp_new'")).scalar()
    sections = [{"code": code, "title": title, "in_archive": f"{usual.get(code, 0)} of {total} past proposals",
                 "items": [{"field": f, "label": rec[f]["label"], "value": rec[f]["value"]} for f in names]}
                for code, title, names in SECTION_MAP]
    unmapped = [{"text": v, "kind": "special_request"} for v in rec["special_requests"]["value"] or []]
    for name, f in rec.items():
        if name.startswith("_"):
            continue
        for s in f["sources"]:
            if s["status"] == "rejected" and s["origin"] == "ai" and s["value_text"]:
                unmapped.append({"text": s["value_text"], "kind": f"no {f['label'].lower()} code", "field": name,
                                 "file_name": s["file_name"], "page": s["page"]})
    return {"sections": sections, "unmapped": unmapped}


@app.get("/api/reference")
def reference(_: Principal = READ):
    return {
        "typology": rows("SELECT * FROM ref.typology ORDER BY code"),
        "service": rows("SELECT * FROM ref.service ORDER BY code"),
        "stage": rows("SELECT * FROM ref.stage ORDER BY seq"),
        "synonyms": rows("SELECT * FROM ref.term_synonym ORDER BY target_table, target_code, synonym"),
        "locations": rows("SELECT l.*, c.name AS country_name, c.national_currency FROM ref.location l "
                          "JOIN ref.country c ON c.code = l.country ORDER BY c.region, l.country, l.city"),
        "rules": rows("SELECT * FROM ref.qualification_rule ORDER BY rule_id"),
        "criticality": rows("SELECT * FROM ref.field_criticality ORDER BY weight DESC, field"),
        "archive": rows("""SELECT p.proposal_id, p.reference, p.title, p.typology, l.city, l.country, p.currency,
                                  p.fee_total_local, p.status, p.dataset_version, p.data_origin
                           FROM archive.proposal p JOIN ref.location l USING (location_id) ORDER BY p.proposal_id"""),
    }


class Synonym(BaseModel):
    synonym: str
    target_table: str
    target_code: str
    scope: str = "global"


@app.post("/api/reference/synonyms")
def add_synonym(s: Synonym, who: Principal = REFERENCE):
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO ref.term_synonym (synonym, target_table, target_code, scope, language)
                             VALUES (lower(:syn), :t, :c, :scope, 'en')
                             ON CONFLICT (org_id, synonym, target_table, scope)
                             DO UPDATE SET target_code = EXCLUDED.target_code"""),
                     dict(syn=s.synonym.strip(), t=s.target_table, c=s.target_code, scope=s.scope))
    return {"ok": True, "by": who.email}


@app.get("/api/observability")
def observability(_: Principal = BACKTEST):
    totals = rows("""SELECT count(*) AS calls, count(DISTINCT trace_id) AS traces, sum(cost_usd) AS cost_usd,
                            sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
                            sum(cache_read_tokens) AS cache_read_tokens,
                            percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                            count(*) FILTER (WHERE validation_error IS NOT NULL) AS rejected,
                            count(*) FILTER (WHERE error IS NOT NULL) AS errors
                     FROM intake.llm_call""")[0]
    by_version = rows("""SELECT model, prompt_version, count(*) AS calls, sum(cost_usd) AS cost_usd,
                                avg(latency_ms)::int AS avg_ms,
                                count(*) FILTER (WHERE validation_error IS NOT NULL) AS rejected
                         FROM intake.llm_call GROUP BY 1, 2 ORDER BY 1, 2""")
    recent = rows("""SELECT c.call_id, c.trace_id, c.attempt, c.model, c.prompt_version, c.input_tokens, c.output_tokens,
                            c.cache_read_tokens, c.latency_ms, c.cost_usd, c.stop_reason, c.validation_error, c.error,
                            c.created_at, d.file_name, r.envelope_id
                     FROM intake.llm_call c LEFT JOIN intake.extraction_run r USING (run_id)
                     LEFT JOIN lineage.source_document d ON d.document_id = r.document_id
                     ORDER BY c.call_id DESC LIMIT 50""")
    return {"totals": totals, "by_version": by_version, "recent": recent}


@app.get("/api/evaluation/latest")
def latest_evaluation(_: Principal = READ):
    run = rows("SELECT * FROM eval.run WHERE dataset_version = :v ORDER BY run_id DESC LIMIT 1",
               v=settings.dataset_version)
    if not run:
        return None
    r = run[0]
    cases = rows("SELECT detail FROM eval.case_result WHERE run_id = :r ORDER BY case_id", r=r["run_id"])
    return {"meta": {"run_at": r["run_at"].isoformat(), "dataset": r["dataset_version"], "model": r["model"],
                     "prompt_version": r["prompt_version"], "engine_version": r["engine_version"]},
            "summary": r["summary"],
            "results": [{k: c["detail"][k] for k in ("proposal_id", "envelope_id", "style", "traps", "fields",
                                                     "conflicts", "lead_status")} for c in cases]}


@app.get("/api/health")
def health():
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar()
    return {"ok": True, "migration": version, "dataset": settings.dataset_version,
            "model": settings.model_extract, "llm_configured": bool(settings.anthropic_api_key),
            "time": datetime.now().isoformat()}
