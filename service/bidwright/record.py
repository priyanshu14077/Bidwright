"""The current record of an envelope: one value per field (or a list), with its
sources, conflicts and flags. Shared by the API and the evaluation."""

from collections import defaultdict

from sqlalchemy import Connection, text

from bidwright.schema import fields

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
        "confidence": float(c["confidence"]) if c["confidence"] is not None else None,
        "origin": c["origin"],
        "kind": c["kind"],
        "status": c["status"],
    }
