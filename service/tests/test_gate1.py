"""Gate 1: statuses, estimates, rules, evidence, questions and decisions. Needs the database; no model calls."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from bidwright import gate1, llm
from bidwright.db import engine, owner_engine, workspace
from bidwright.engine import run_engine
from bidwright.intake.ingest import delete_envelope, ingest

MONDAY = datetime(2026, 11, 16, 10, 42, tzinfo=timezone(timedelta(hours=4)))


def new_request(org, text_body: str, received_at=MONDAY) -> tuple[int, int]:
    with workspace(org), engine.begin() as conn:
        e, _ = ingest(conn, [(f"msg-{uuid.uuid4().hex}.txt", text_body.encode())], "whatsapp", received_at=received_at)
        d = conn.execute(text("SELECT document_id FROM lineage.source_document WHERE envelope_id = :e"),
                         dict(e=e)).scalar_one()
    return e, d


def add_doc(org, e: int, body: str, received_at) -> int:
    with workspace(org), engine.begin() as conn:
        ingest(conn, [(f"reply-{uuid.uuid4().hex}.txt", body.encode())], "whatsapp", received_at=received_at,
               envelope_id=e)
        return conn.execute(text("SELECT max(document_id) FROM lineage.source_document WHERE envelope_id = :e"),
                            dict(e=e)).scalar_one()


def say(org, e, d, field, value_text, number=None, unit="none", norm=None, conf=0.9):
    with workspace(org), engine.begin() as conn:
        conn.execute(text("""INSERT INTO lineage.field_value (envelope_id, field, value, document_id, quote, confidence,
                             origin, kind, status) VALUES (:e, :f, CAST(:v AS jsonb), :d, :q, :c, 'ai', 'fact', 'proposed')"""),
                     dict(e=e, f=field, d=d, q=value_text, c=conf,
                          v=json.dumps(dict(value_text=value_text, number=number, unit=unit, normalized_text=norm,
                                            line_ids=["p1:1"], revises=False))))


def assess(org, e) -> dict:
    with workspace(org):
        run_engine(e)
        with engine.connect() as conn:
            return gate1.view(conn, e)


def field(v: dict, name: str) -> dict:
    return next(f for f in v["fields"] if f["name"] == name)


@pytest.fixture
def demo_requests(demo_org):
    made = []
    yield made
    with workspace(demo_org), owner_engine.begin() as conn:
        for e in made:
            delete_envelope(conn, e)


def tower(org, made) -> tuple[int, int]:
    """Demo step 1: 'Marina Crest again, concept for a resi tower in JVC, ~30 floors, by Thursday next week'."""
    e, d = new_request(org, "Hi, Marina Crest again 🙂 we want concept for a resi tower in JVC, ~30 floors.")
    made.append(e)
    say(org, e, d, "client_name", "Marina Crest")
    say(org, e, d, "city", "JVC", norm="Dubai, AE")
    say(org, e, d, "typology", "resi tower", norm="residential", conf=0.7)
    say(org, e, d, "services", "concept", norm="architecture", conf=0.7)
    say(org, e, d, "stage_package", "concept", norm="concept", conf=0.7)
    say(org, e, d, "storeys", "~30 floors", number=30, conf=0.7)
    say(org, e, d, "submission_deadline", "Thursday next week", norm="2026-11-26", conf=0.7)
    return e, d


def test_a_one_liner_is_clarify_with_every_conclusion_sourced(demo_org, demo_requests):
    e, _ = tower(demo_org, demo_requests)
    v = assess(demo_org, e)
    assert v["recommendation"] == "clarify" and [r["name"] for r in v["rules"]] == ["low_completeness"]
    assert "repeat_client" in [r["name"] for r in v["flags"]]
    for f in v["fields"]:  # no conclusion without evidence, and every piece of evidence says why
        assert (f["status"] == "missing") == (not f["evidence"]), f["name"]
        assert all(x["why"] for x in f["evidence"])
    assert field(v, "country")["status"] == "found" and field(v, "country")["value"] == "AE"
    assert "Dubai" in field(v, "country")["evidence"][0]["why"]
    assert field(v, "client_status")["value"] == "repeat" and field(v, "client_status")["evidence"][0]["kind"] == "comparable"
    gfa = field(v, "gfa_m2")
    assert gfa["status"] == "estimated" and gfa["estimate"]["comparables"] and gfa["range_display"]
    assert gfa["evidence"][0]["kind"] == "comparable" and "30 floors stated" in gfa["evidence"][0]["why"]
    assert field(v, "site_area_m2")["status"] == "missing"
    assert field(v, "billing_currency")["status"] == "estimated" and field(v, "billing_currency")["value"] == "AED"
    assert v["questions"]["items"][:2] == [
        {"field": "gfa_m2", "question": gate1.FALLBACK["gfa_m2"], "worded": False},
        {"field": "site_area_m2", "question": gate1.FALLBACK["site_area_m2"], "worded": False}]
    low = next(r for r in v["rules"] if r["name"] == "low_completeness")
    assert low["evidence"][0]["kind"] == "rule" and "Site area" in low["why"]


def test_the_clients_reply_turns_estimates_into_answers(demo_org, demo_requests):
    e, _ = tower(demo_org, demo_requests)
    before = assess(demo_org, e)
    reply = add_doc(demo_org, e, "Plot is 2,400 m². And interiors for the amenity floors.", MONDAY + timedelta(days=1))
    say(demo_org, e, reply, "site_area_m2", "2,400 m²", number=2400, unit="m2")
    after = assess(demo_org, e)
    site = field(after, "site_area_m2")
    assert site["status"] == "found" and site["evidence"][0]["kind"] == "reply"
    assert site["evidence"][0]["why"].startswith("From the client's reply of 17 Nov")
    gfa = field(after, "gfa_m2")
    assert gfa["estimate"]["basis"] == "site_area_m2" and "Site 2,400 × median plot ratio" in gfa["estimate"]["method"]
    assert after["completeness"]["score"] > before["completeness"]["score"]
    assert after["recommendation"] == "go"


def test_deadline_feasibility_and_currency_rules(demo_org, demo_requests):
    e, d = new_request(demo_org, "Rough number for a 4-acre plot in Pune, mixed use", MONDAY)
    demo_requests.append(e)
    for args in [("city", "Pune", None, "none", "Pune, IN"), ("typology", "mixed use", None, "none", "mixed_use"),
                 ("request_type", "rough number", None, "none", "feasibility_estimate"),
                 ("site_area_m2", "4-acre", 4, "acre", None),
                 ("submission_deadline", "Wednesday", None, "none", "2026-11-18")]:
        say(demo_org, e, d, *args[:2], number=args[2], unit=args[3], norm=args[4])
    v = assess(demo_org, e)
    fired = {r["name"] for r in v["rules"]}
    assert {"deadline_too_close", "feasibility_only", "currency_unconfirmed"} <= fired
    assert v["recommendation"] == "no-go" and v["questions"]["items"] == []  # nothing to ask on a no-go
    assert v["routed_studio"] == "SIN" and "cross_entity" in {r["name"] for r in v["flags"]}
    assert field(v, "stage_package")["value"] == ["concept"]  # a rough number is priced for concept by default
    assert "Due Wed 18 Nov: 2 working days" in next(r["why"] for r in v["rules"] if r["name"] == "deadline_too_close")
    say(demo_org, e, d, "fee_currency", "quote in USD", norm="USD")
    assert "currency_unconfirmed" not in {r["name"] for r in assess(demo_org, e)["rules"]}


def test_a_named_past_project_fills_scope(demo_org, demo_requests):
    e, d = new_request(demo_org, "Similar to Bay Square: another mid-rise office in Dubai Hills, around 30,000 m² GFA.")
    demo_requests.append(e)
    say(demo_org, e, d, "reference_project", "Bay Square")
    say(demo_org, e, d, "typology", "office", norm="office")
    say(demo_org, e, d, "city", "Dubai Hills", norm="Dubai, AE")
    say(demo_org, e, d, "gfa_m2", "30,000 m²", number=30000, unit="m2")
    v = assess(demo_org, e)
    services = field(v, "services")
    assert services["status"] == "estimated" and set(services["value"]) == {"architecture", "interior_design"}
    assert services["estimate"]["comparables"] == ["NBS-DXB-2026-006"] and services["estimate"]["confidence"] == "high"
    assert v["reference_project"]["reference"] == "NBS-DXB-2026-006"


def test_a_conclusion_whose_evidence_is_gone_is_not_shown(demo_org, demo_requests):
    e, _ = tower(demo_org, demo_requests)
    assess(demo_org, e)
    with workspace(demo_org), engine.begin() as conn:
        conn.execute(text("DELETE FROM lineage.evidence WHERE envelope_id = :e AND subject = 'field:typology'"), dict(e=e))
        v = gate1.view(conn, e)
    assert field(v, "typology")["status"] == "missing" and "not shown" in field(v, "typology")["note"]


def test_decisions_are_logged_and_a_decline_needs_a_reason(stranger):
    client, org = stranger
    e, d = new_request(org, "Hi, can you quote a hotel in Doha?")
    say(org, e, d, "city", "Doha", norm="Doha, QA")
    with workspace(org):
        run_engine(e)
    v = client.get(f"/api/envelopes/{e}/gate1").json()
    assert v["decision"] is None and "Unpaid feasibility" in v["decline_reasons"]
    assert client.post(f"/api/envelopes/{e}/gate1/decision", json={"decision": "decline"}).status_code == 422
    r = client.post(f"/api/envelopes/{e}/gate1/decision",
                    json={"decision": "decline", "reason": "Unpaid feasibility", "note": "budget figure only"})
    assert r.status_code == 200 and r.json()["decision"]["decision"] == "decline"
    assert r.json()["decision"]["recommendation"] == v["recommendation"]
    with workspace(org), engine.begin() as conn, pytest.raises(Exception):
        conn.execute(text("UPDATE intake.gate_decision SET reason = 'x' WHERE envelope_id = :e"), dict(e=e))


def test_worded_questions_cannot_invent_figures(stranger, monkeypatch):
    _, org = stranger
    e, d = new_request(org, "Can you price an office in Doha?")
    say(org, e, d, "city", "Doha", norm="Doha, QA")
    say(org, e, d, "typology", "office", norm="office")
    with workspace(org):
        run_engine(e)
        with engine.connect() as conn:
            ask = gate1.view(conn, e)["questions"]["items"]
    fields = [q["field"] for q in ask]
    answers = []

    def fake_call(*, validate, **_):
        data = answers.pop(0)
        validate(data)
        return llm.Result(data, 1, 1)

    monkeypatch.setattr(llm, "structured_call", fake_call)
    answers.append({"questions": [{"field": f, "question": "Is it about 45,000 m²?"} for f in fields], "message": "Hi"})
    with workspace(org), pytest.raises(llm.ValidationFailed, match="figures that are not in the input"):
        gate1.write_followup(e)
    answers.append({"questions": [{"field": f, "question": f"Could you tell us the {f}?"} for f in fields],
                    "message": "Hello,\n1. ...\nThank you."})
    with workspace(org):
        gate1.write_followup(e)
        with engine.connect() as conn:
            q = gate1.view(conn, e)["questions"]
    assert q["worded"] and all(i["worded"] for i in q["items"])


def test_a_request_named_after_its_file_takes_the_project_name(stranger):
    _, org = stranger
    e, d = new_request(org, "RFP for Wadi Hanifa Offices, Riyadh")
    with workspace(org), engine.begin() as conn:
        stem = conn.execute(text("SELECT title FROM intake.envelope WHERE envelope_id = :e"), dict(e=e)).scalar()
    assert stem.startswith("msg-")  # ingested under the file's name
    say(org, e, d, "project_name", "Wadi Hanifa Offices")
    assess(org, e)
    with workspace(org), engine.begin() as conn:
        assert conn.execute(text("SELECT title FROM intake.envelope WHERE envelope_id = :e"), dict(e=e)).scalar() == "Wadi Hanifa Offices"
        conn.execute(text("UPDATE intake.envelope SET title = 'Marina tender' WHERE envelope_id = :e"), dict(e=e))
    assess(org, e)  # a title someone chose is kept
    with workspace(org), engine.connect() as conn:
        assert conn.execute(text("SELECT title FROM intake.envelope WHERE envelope_id = :e"), dict(e=e)).scalar() == "Marina tender"


def test_only_the_areas_a_service_is_priced_on_are_estimated():
    from bidwright.gate1 import areas_to_estimate
    assert areas_to_estimate(["masterplan"], "mixed_use") == {"site_area_m2"}  # read as mixed use, still per hectare
    assert areas_to_estimate(["architecture"], "masterplan") == {"site_area_m2"}
    assert areas_to_estimate(["interior_design"], "office") == {"gfa_m2"}  # a fit-out needs no site area
    assert areas_to_estimate(["architecture", "interior_design"], "office") == {"gfa_m2", "site_area_m2"}
    assert areas_to_estimate([], None) == {"gfa_m2", "site_area_m2"}
