"""HTTP API for the Bidwright app and for integrations (n8n, mail ingest).

Every endpoint asks for one permission (see tenancy.PERMISSIONS) and runs inside
the caller's workspace; row-level security does the rest.

    uv run uvicorn bidwright.api:app --reload --port 8000
"""

import json
from datetime import datetime

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field as PField
from sqlalchemy import text

from bidwright import record
from bidwright.auth import require, router as auth_router
from bidwright.config import REPO_ROOT, settings
from bidwright.db import engine, in_workspace
from bidwright.engine import comparables, run_engine
from bidwright.extraction import extract_envelope
from bidwright.intake.ingest import ingest
from bidwright.schema import SCHEMA_VERSION, fields
from bidwright.storage import blob_store
from bidwright.tenancy import Principal

app = FastAPI(title="Bidwright", version="0.2.0")
app.include_router(auth_router)

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
               (SELECT max(version) FROM intake.opportunity o WHERE o.envelope_id = e.envelope_id) AS confirmed_version
        FROM intake.envelope e LEFT JOIN ref.location l USING (location_id)
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
