"""The current record of an envelope: one value per field (or a list), with its
sources, conflicts and flags. Shared by the API and the evaluation."""

from collections import defaultdict

import json

from fastapi import HTTPException
from sqlalchemy import Connection, text

from bidwright.schema import SCHEMA_VERSION, fields

ENGINE_FIELDS = {
    "location_tier": "Location tier",
    "billing_currency": "Billing currency",
    "routed_studio": "Studio",
    "client_status": "Client status",
}
ORIGIN_RANK = {"human": 0, "ai": 1, "engine": 2}


def build(conn: Connection, envelope_id: int) -> dict:
    rows = conn.execute(text("""
        SELECT fv.field_value_id, fv.field, fv.value, fv.normalized, fv.document_id, d.file_name, d.doc_class,
               fv.page, fv.quote, fv.bbox, fv.confidence, fv.origin, fv.kind, fv.status
        FROM lineage.field_value fv LEFT JOIN lineage.source_document d USING (document_id)
        WHERE fv.envelope_id = :e ORDER BY fv.field_value_id"""), dict(e=envelope_id)).all()
    conflicts = {c.field: dict(c._mapping) for c in conn.execute(text(
        "SELECT conflict_id, field, field_value_ids, summary, status, resolved_value_id FROM intake.field_conflict "
        "WHERE envelope_id = :e AND status = 'open'"), dict(e=envelope_id))}
    flags = defaultdict(list)
    for f in conn.execute(text("SELECT field, rule, severity, message, evidence, field_value_id FROM intake.engine_flag "
                               "WHERE envelope_id = :e ORDER BY flag_id"), dict(e=envelope_id)):
        flags[f.field].append(dict(f._mapping))

    by_field = defaultdict(list)
    for r in rows:
        by_field[r.field].append(dict(r._mapping))

    schema = fields()
    out = {}
    for name in [*schema, *ENGINE_FIELDS]:
        f = schema.get(name)
        cands = by_field.get(name, [])
        live = [c for c in cands if c["status"] in ("proposed", "confirmed") and c["normalized"] is not None]
        human = [c for c in live if c["origin"] == "human"]
        facts = human or [c for c in live if c["kind"] == "fact"]
        facts.sort(key=lambda c: ORIGIN_RANK[c["origin"]])
        suggestion = next((c for c in live if c["kind"] == "suggestion"), None)
        conflict = conflicts.get(name) if not human else None
        if f and f.is_list:
            value = [c["normalized"]["value"] for c in facts]
        elif conflict:
            first = next((c for c in facts if c["field_value_id"] == conflict["field_value_ids"][0]), facts[0] if facts else None)
            value = first["normalized"]["value"] if first else None
        else:
            value = facts[0]["normalized"]["value"] if facts else None
        out[name] = {
            "label": f.label if f else ENGINE_FIELDS[name],
            "group": f.group if f else "Enrichment",
            "kind": f.kind if f else "engine",
            "is_list": bool(f and f.is_list),
            "options": list(f.options) if f else [],
            "value": value,
            "suggestion": suggestion["normalized"]["value"] if suggestion and value in (None, []) else None,
            "suggestion_evidence": (suggestion["value"] or {}).get("evidence") if suggestion and value in (None, []) else None,
            "sources": [source(c) for c in cands],
            "conflict": conflict,
            "flags": flags.get(name, []),
            "edited": bool(human),
        }
    out["_flags"] = flags.get(None, [])
    return out


def source(c: dict) -> dict:
    value = c["value"] or {}
    return {
        "field_value_id": c["field_value_id"],
        "value_text": value.get("value_text"),
        "normalized": (c["normalized"] or {}).get("value"),
        "notes": (c["normalized"] or {}).get("notes", []),
        "evidence": value.get("evidence"),
        "reason": value.get("reason"),
        "document_id": c["document_id"],
        "file_name": c["file_name"],
        "doc_class": c["doc_class"],
        "page": c["page"],
        "quote": c["quote"],
        "bbox": c["bbox"],
        "quote_verbatim": value.get("quote_verbatim"),
        "span": value.get("span"),
        "confidence": float(c["confidence"]) if c["confidence"] is not None else None,
        "origin": c["origin"],
        "kind": c["kind"],
        "status": c["status"],
    }


def confirm(conn: Connection, envelope_id: int, email: str, reason: str, extra: dict | None = None) -> int:
    """Lock the current record as the next confirmed version (gate 2 of intake). extra adds to the snapshot,
    e.g. the values priced as assumptions. Returns the version number."""
    open_conflicts = conn.execute(text("SELECT count(*) FROM intake.field_conflict WHERE envelope_id = :e "
                                       "AND status = 'open'"), dict(e=envelope_id)).scalar()
    if open_conflicts:
        raise HTTPException(409, f"{open_conflicts} open conflict(s) must be resolved before confirming")
    rec = build(conn, envelope_id)
    snapshot = {k: v["value"] for k, v in rec.items() if not k.startswith("_")}
    env = conn.execute(text("SELECT lead_status, completeness FROM intake.envelope WHERE envelope_id = :e"),
                       dict(e=envelope_id)).one()
    snapshot |= {"lead_status": env.lead_status, "completeness": float(env.completeness or 0)} | (extra or {})
    version = (conn.execute(text("SELECT max(version) FROM intake.opportunity WHERE envelope_id = :e"),
                            dict(e=envelope_id)).scalar() or 0) + 1
    conn.execute(text("INSERT INTO intake.opportunity (envelope_id, version, record, schema_version, confirmed_by) "
                      "VALUES (:e, :v, CAST(:r AS jsonb), :sv, :u)"),
                 dict(e=envelope_id, v=version, r=json.dumps(snapshot, default=str), sv=SCHEMA_VERSION, u=email))
    ids = conn.execute(text("""UPDATE lineage.field_value SET status = 'confirmed' WHERE envelope_id = :e
                               AND status = 'proposed' RETURNING field_value_id"""), dict(e=envelope_id)).scalars().all()
    conn.execute(text("""INSERT INTO lineage.field_change (field, new_value, action, reason, changed_by)
                         VALUES ('*', CAST(:v AS jsonb), 'confirm', :r, :u)"""),
                 dict(v=json.dumps({"envelope_id": envelope_id, "version": version, "values_locked": len(ids)}),
                      r=reason, u=email))
    conn.execute(text("UPDATE intake.envelope SET status = 'confirmed', updated_at = now() WHERE envelope_id = :e"),
                 dict(e=envelope_id))
    return version
