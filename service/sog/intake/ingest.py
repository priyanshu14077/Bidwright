"""Intake: one envelope per RFP pack, every file stored unchanged with lineage.

A pack can arrive as loose files, a zip, or an email with attachments. Files
already seen (same sha256) are not processed twice: the upload is linked to
the envelope that first received them.
"""

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import Connection, text

from sog.config import settings
from sog.intake import reading
from sog.storage import blob_store

MIME = {".pdf": "application/pdf", ".eml": "message/rfc822", ".zip": "application/zip",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def expand(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes, str | None]]:
    """Flatten zips and email attachments. Returns (name, data, parent_name)."""
    out: list[tuple[str, bytes, str | None]] = []
    for name, data in files:
        suffix = Path(name).suffix.lower()
        if suffix == ".zip":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                members = [m for m in z.infolist() if not m.is_dir() and not Path(m.filename).name.startswith(".")
                           and "__MACOSX" not in m.filename]
                out += [(item[0], item[1], item[2] or name)
                        for item in expand([(Path(m.filename).name, z.read(m)) for m in members])]
        elif suffix == ".eml":
            out.append((name, data, None))
            _, _, attachments = reading.parse_email(data)
            out += [(a, b, p or name) for a, b, p in expand(attachments)]
        else:
            out.append((name, data, None))
    return out


def ingest(conn: Connection, files: list[tuple[str, bytes]], channel: str, sender: str | None = None,
           title: str | None = None, received_at: datetime | None = None) -> tuple[int, bool]:
    """Store a pack. Returns (envelope_id, created)."""
    items = expand(files)
    stored = [(name, data, parent, *blob_store.put(data, name)) for name, data, parent in items]
    shas = [s[3] for s in stored]

    existing = conn.execute(
        text("SELECT envelope_id FROM lineage.source_document WHERE sha256 = ANY(:shas) AND envelope_id IS NOT NULL "
             "ORDER BY document_id LIMIT 1"), dict(shas=shas)).scalar()
    created = existing is None
    if created:
        if title is None:
            title = email_title(items) or Path(files[0][0]).stem
        envelope_id = conn.execute(
            text("INSERT INTO intake.envelope (title, channel, sender, received_at, dataset_version, status) "
                 "VALUES (:title, :channel, :sender, COALESCE(:received_at, now()), :v, 'reading') RETURNING envelope_id"),
            dict(title=title, channel=channel, sender=sender, received_at=received_at, v=settings.sog_dataset_version),
        ).scalar_one()
    else:
        envelope_id = existing

    ids_by_name: dict[str, int] = {}
    for name, data, parent, sha, uri in stored:
        known = conn.execute(text("SELECT document_id FROM lineage.source_document WHERE sha256 = :sha"),
                             dict(sha=sha)).scalar()
        if known:
            ids_by_name[name] = known
            continue
        supported = reading.is_supported(name)
        pdf = reading.render_pdf(name, data) if supported else None
        render_uri = blob_store.put(pdf, Path(name).stem + ".render.pdf")[1] if pdf else None
        pages = reading.read_layout(pdf) if pdf else []
        doc_id = conn.execute(
            text("""INSERT INTO lineage.source_document
                    (envelope_id, parent_document_id, channel, file_name, mime_type, size_bytes, sha256, blob_uri,
                     render_uri, sender, received_at, doc_class, page_count, supported)
                    VALUES (:env, :parent, :channel, :name, :mime, :size, :sha, :uri, :render, :sender,
                            COALESCE(:received_at, now()), :doc_class, :pages, :supported)
                    RETURNING document_id"""),
            dict(env=envelope_id, parent=ids_by_name.get(parent) if parent else None, channel=channel, name=name,
                 mime=MIME.get(Path(name).suffix.lower()), size=len(data), sha=sha, uri=uri, render=render_uri,
                 sender=sender, received_at=received_at, pages=len(pages) or None,
                 doc_class="email" if name.lower().endswith(".eml") else ("unsupported" if not supported else None),
                 supported=supported and pdf is not None),
        ).scalar_one()
        ids_by_name[name] = doc_id
        if pages:
            conn.execute(
                text("INSERT INTO lineage.page VALUES (:d, :n, :w, :h, CAST(:lines AS jsonb))"),
                [dict(d=doc_id, n=p.page_no, w=p.width, h=p.height,
                      lines=json.dumps([{"id": ln.line_id, "text": ln.text, "bbox": ln.bbox} for ln in p.lines]))
                 for p in pages],
            )
    conn.execute(text("UPDATE intake.envelope SET updated_at = now(), "
                      "status = CASE WHEN status = 'reading' THEN 'received' ELSE status END WHERE envelope_id = :e"),
                 dict(e=envelope_id))
    return envelope_id, created


def email_title(items: list[tuple[str, bytes, str | None]]) -> str | None:
    for name, data, _ in items:
        if name.lower().endswith(".eml"):
            headers, _, _ = reading.parse_email(data)
            return headers.get("Subject") or None
    return None


def delete_envelope(conn, envelope_id: int) -> None:
    conn.execute(text("ALTER TABLE lineage.field_change DISABLE TRIGGER field_change_append_only"))
    conn.execute(text("ALTER TABLE intake.opportunity DISABLE TRIGGER opportunity_locked"))
    conn.execute(text("""DELETE FROM lineage.field_change WHERE old_field_value_id IN
                         (SELECT field_value_id FROM lineage.field_value WHERE envelope_id = :e)
                         OR new_field_value_id IN (SELECT field_value_id FROM lineage.field_value WHERE envelope_id = :e)"""),
                 dict(e=envelope_id))
    conn.execute(text("DELETE FROM intake.opportunity WHERE envelope_id = :e"), dict(e=envelope_id))
    conn.execute(text("ALTER TABLE lineage.field_change ENABLE TRIGGER field_change_append_only"))
    conn.execute(text("ALTER TABLE intake.opportunity ENABLE TRIGGER opportunity_locked"))
    for sql in ["DELETE FROM intake.engine_flag WHERE envelope_id = :e",
                "DELETE FROM intake.field_conflict WHERE envelope_id = :e",
                "DELETE FROM lineage.field_value WHERE envelope_id = :e",
                "DELETE FROM intake.llm_call WHERE run_id IN (SELECT run_id FROM intake.extraction_run WHERE envelope_id = :e)",
                "DELETE FROM intake.extraction_run WHERE envelope_id = :e",
                "DELETE FROM lineage.source_document WHERE envelope_id = :e",
                "DELETE FROM intake.envelope WHERE envelope_id = :e"]:
        conn.execute(text(sql), dict(e=envelope_id))
