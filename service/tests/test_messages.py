"""Requests that arrive as messages, notes and screenshots. Needs the database; no model calls."""

import json
import uuid

import pytest
from sqlalchemy import text

from bidwright import extraction
from bidwright.db import engine, workspace
from bidwright.engine import run_engine
from bidwright.intake import ocr, reading
from bidwright.intake.ingest import ingest
from bidwright.record import build
from bidwright.storage import blob_store

THREAD = ("[19/11/2026, 13:10:22] Faisal: Can you price an office building in Business Bay? G+20\n"
          "[19/11/2026, 13:11:48] Faisal: GFA I think 45k, will confirm\n")


def test_text_lines_are_exact_slices_of_the_message_with_their_offsets():
    body = "السلام عليكم، plot approx 18,000 m2\n\n" + "word " * 60 + "\nend"
    lines = [ln for p in reading.text_pages(body) for ln in p.lines]
    assert all(body[ln.start:ln.start + len(ln.text)] == ln.text for ln in lines)
    assert lines[0].text.startswith("السلام") and lines[-1].text == "end"
    assert all(len(ln.text) <= reading.WRAP_AT for ln in lines)


def test_a_quote_is_located_in_the_message():
    body = "Plot 2,800 m2\nGFA I think 45k, will confirm\n"
    lines = {ln.line_id: {"start": ln.start, "text": ln.text} for p in reading.text_pages(body) for ln in p.lines}
    span = extraction.text_span(body, lines, {"line_ids": ["p1:2"], "quote": "45k"})
    assert body[span[0]:span[1]] == "45k"
    assert extraction.text_span(body, lines, {"line_ids": ["p1:2"], "quote": "not there"}) is None


def test_a_pasted_message_and_the_clients_reply_are_text_sources(stranger, monkeypatch):
    client, _ = stranger
    read = []
    monkeypatch.setattr("bidwright.api.extract_envelope", lambda e: read.append(e))
    r = client.post("/api/envelopes/text", json={"text": THREAD, "channel": "whatsapp", "sender": "Faisal"})
    assert r.status_code == 200 and r.json()["created"], r.text
    e = r.json()["envelope_id"]
    again = client.post("/api/envelopes/text", json={"text": THREAD, "channel": "whatsapp"}).json()
    assert again == {"envelope_id": e, "created": False}  # the same message twice is one request
    reply = client.post(f"/api/envelopes/{e}/text", json={"text": "GFA is 45,000 m2, confirmed", "channel": "whatsapp"})
    assert reply.status_code == 200
    assert read == [e, e]  # read when it arrived, and again with the reply
    docs = client.get(f"/api/envelopes/{e}").json()["documents"]
    assert [d["channel"] for d in docs] == ["whatsapp", "whatsapp"] and docs[0]["file_name"].startswith("WhatsApp")
    assert client.get(f"/api/documents/{docs[0]['document_id']}/text").text == THREAD
    assert client.post("/api/envelopes/999999999/text", json={"text": "x"}).status_code == 404


class FakeReader:
    def read(self, file_name, data, *, run_id, trace_id):
        return "[10:02] Priya: Site 6,500 m2, total GFA approx 58,000 m2\n"


def test_a_screenshot_is_transcribed_once_into_a_text_source(stranger, monkeypatch):
    _, org = stranger
    monkeypatch.setattr(ocr, "image_reader", FakeReader())
    with workspace(org), engine.begin() as conn:
        e, _ = ingest(conn, [("Screenshot.png", b"\x89PNG fake " + uuid.uuid4().bytes)], "whatsapp")
        doc = conn.execute(text("SELECT document_id, supported, render_uri FROM lineage.source_document "
                                "WHERE envelope_id = :e"), dict(e=e)).one()
    assert doc.supported and doc.render_uri is None  # stored, waiting to be read
    with workspace(org):
        extraction.read_image(e, doc.document_id, uuid.uuid4())
        with engine.connect() as conn:
            d = conn.execute(text("SELECT text_uri, render_uri, page_count FROM lineage.source_document "
                                  "WHERE document_id = :d"), dict(d=doc.document_id)).one()
            lines = extraction.document_lines(conn, doc.document_id)
            run = conn.execute(text("SELECT kind, status FROM intake.extraction_run WHERE document_id = :d"),
                               dict(d=doc.document_id)).one()
    assert blob_store.get(d.text_uri).decode().startswith("[10:02] Priya") and d.render_uri and d.page_count == 1
    assert lines[0]["text"].startswith("[10:02] Priya") and lines[0]["start"] == 0
    assert tuple(run) == ("read", "succeeded")


def mention(conn, e, d, field, value_text, number=None, unit="none", norm=None, conf=0.9, revises=False):
    conn.execute(text("""INSERT INTO lineage.field_value (envelope_id, field, value, document_id, quote, confidence,
                         origin, kind, status) VALUES (:e, :f, CAST(:v AS jsonb), :d, :q, :c, 'ai', 'fact', 'proposed')"""),
                 dict(e=e, f=field, d=d, q=value_text, c=conf,
                      v=json.dumps(dict(value_text=value_text, number=number, unit=unit, normalized_text=norm,
                                        line_ids=["p1:1"], revises=revises))))


@pytest.fixture
def message(stranger):
    _, org = stranger
    with workspace(org), engine.begin() as conn:
        e, _ = ingest(conn, [(f"msg-{uuid.uuid4().hex}.txt", THREAD.encode())], "whatsapp")
        d = conn.execute(text("SELECT document_id FROM lineage.source_document WHERE envelope_id = :e"),
                         dict(e=e)).scalar_one()
    return org, e, d


def test_engine_takes_country_from_the_city_and_honours_the_asked_currency(message):
    org, e, d = message
    with workspace(org), engine.begin() as conn:
        mention(conn, e, d, "city", "Business Bay", norm="Dubai, AE")
        mention(conn, e, d, "fee_currency", "Fee in AED please", norm="AED")
        mention(conn, e, d, "gfa_m2", "I think 45k, will confirm", number=45000, unit="m2", conf=0.4)
    with workspace(org):
        run_engine(e)
        with engine.connect() as conn:
            rec = build(conn, e)
    assert rec["country"]["value"] == "AE" and rec["city"]["value"] == "Dubai"
    assert rec["billing_currency"]["value"] == "AED"
    assert rec["billing_currency"]["sources"][0]["evidence"]["basis"] == "the client asked for fees in AED"
    assert [f["rule"] for f in rec["gfa_m2"]["flags"]] == ["unconfirmed_value"]


def test_a_city_outside_the_practices_markets_still_names_its_country(message):
    org, e, d = message
    with workspace(org), engine.begin() as conn:
        mention(conn, e, d, "city", "Nairobi", norm="Nairobi, KE")
    with workspace(org):
        run_engine(e)
        with engine.connect() as conn:
            rec = build(conn, e)
            rules = conn.execute(text("SELECT rule FROM intake.engine_flag WHERE envelope_id = :e"),
                                 dict(e=e)).scalars().all()
    assert rec["country"]["value"] == "KE" and "rule:market_not_served" in rules


def test_a_revision_replaces_the_earlier_value_instead_of_conflicting(message):
    org, e, d = message
    with workspace(org), engine.begin() as conn:
        mention(conn, e, d, "gfa_m2", "approx. 120,000 m² GFA", number=120000, unit="m2")
        mention(conn, e, d, "gfa_m2", "GFA reduced to 95,000 m²", number=95000, unit="m2", revises=True)
    with workspace(org):
        run_engine(e)
        with engine.connect() as conn:
            rec = build(conn, e)
    assert rec["gfa_m2"]["value"] == 95000 and rec["gfa_m2"]["conflict"] is None
    replaced = [s for s in rec["gfa_m2"]["sources"] if s["normalized"] == 120000]
    assert replaced[0]["status"] == "superseded" and "replaced by a later revision" in replaced[0]["notes"]

