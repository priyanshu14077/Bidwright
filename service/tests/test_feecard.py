"""The fee card: priced on stated values plus labelled assumptions, a range that widens with them, and the
director's decision. Runs in the demo workspace (it needs the archive); no model calls."""

import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from bidwright import feecard
from bidwright.db import engine, owner_engine, workspace
from bidwright.intake.ingest import delete_envelope
from bidwright.tenancy import Principal
from test_gate1 import MONDAY, add_doc, assess, new_request, say, tower


@pytest.fixture
def made(demo_org):
    envelopes = []
    yield envelopes
    with workspace(demo_org), owner_engine.begin() as conn:
        for e in envelopes:
            delete_envelope(conn, e)


@pytest.fixture
def director(demo_org):
    return Principal(uuid.uuid4(), "director@example.com", "Director", demo_org, "owner")


def card(org, e) -> dict:
    with workspace(org), engine.connect() as conn:
        return feecard.view(conn, e)


def replied_tower(org, made) -> int:
    e, _ = tower(org, made)
    reply = add_doc(org, e, "Plot is 2,400 m². Interiors for the amenity floors too.", MONDAY + timedelta(days=1))
    say(org, e, reply, "site_area_m2", "2,400 m²", number=2400, unit="m2")
    say(org, e, reply, "services", "interiors", norm="interior_design")
    assess(org, e)
    return e


def test_a_one_liner_is_priced_with_its_assumptions_widening_the_range(demo_org, made):
    e = replied_tower(demo_org, made)
    c = card(demo_org, e)
    assert c["status"] == "priced" and c["currency"] == "AED" and c["opening"] > 0
    assert c["band"]["low"] <= c["won"]["low"] <= c["opening"] <= c["won"]["high"] <= c["band"]["high"]
    assumed = {a["field"]: a for a in c["assumptions"]}
    assert {"gfa_m2", "fitout_area_m2", "billing_currency"} <= set(assumed)
    assert assumed["gfa_m2"]["swing"]["up"] > 0 and assumed["gfa_m2"]["evidence"][0]["kind"] == "comparable"
    assert assumed["fitout_area_m2"]["evidence"] and "itself estimated" in assumed["fitout_area_m2"]["evidence"][0]["why"]
    assert [s["label"] for s in c["stages"]] == ["Concept Design"] and sum(s["amount"] for s in c["stages"]) == c["opening"]
    assert c["why"][0]["label"] == "Construction cost (USD per m²)" and c["why"][0]["source"]
    assert c["comparables"]["items"] and c["decision"] is None and c["proposal"] is None


def test_default_stages_show_what_the_next_stage_would_add(demo_org, made):
    e, d = new_request(demo_org, "Can you price a 20,000 m2 office in Dubai?")
    made.append(e)
    say(demo_org, e, d, "city", "Dubai", norm="Dubai, AE")
    say(demo_org, e, d, "typology", "office", norm="office")
    say(demo_org, e, d, "gfa_m2", "20,000 m2", number=20000, unit="m2")
    assess(demo_org, e)
    c = card(demo_org, e)
    assert any(a["field"] == "stage_package" for a in c["assumptions"])
    assert c["stage_options"][0]["label"] == "Design Development" and c["stage_options"][0]["adds"] > 0


def test_pricing_stops_and_names_what_it_needs(demo_org, made):
    e, d = new_request(demo_org, "Fee for a project in Dubai please")
    made.append(e)
    say(demo_org, e, d, "city", "Dubai", norm="Dubai, AE")
    assess(demo_org, e)
    c = card(demo_org, e)
    assert c["status"] == "missing" and "Typology" in [m["label"] for m in c["missing"]]


def test_an_override_needs_a_reason_and_becomes_the_proposals_total(demo_org, made, director):
    e = replied_tower(demo_org, made)
    with workspace(demo_org):
        with pytest.raises(HTTPException) as no_reason:
            feecard.decide(e, feecard.FeeDecision(decision="override", amount=1_900_000), director)
        assert no_reason.value.status_code == 422
        c = feecard.decide(e, feecard.FeeDecision(decision="override", amount=1_900_000,
                                                  reason="round figure agreed with the client"), director)
        with pytest.raises(HTTPException) as twice:
            feecard.decide(e, feecard.FeeDecision(decision="approve"), director)
        assert twice.value.status_code == 409
        with engine.connect() as conn:
            lines = conn.execute(text("""SELECT l.amount_local, l.engine_amount_local, l.note FROM proposal.line l
                                         JOIN proposal.version v USING (version_id) WHERE v.proposal_id = :p"""),
                                 dict(p=c["proposal"]["proposal_id"])).all()
            snapshot = conn.execute(text("SELECT record FROM intake.opportunity WHERE envelope_id = :e"),
                                    dict(e=e)).scalar()
    assert c["decision"]["decision"] == "override" and float(c["decision"]["amount"]) == 1_900_000
    assert float(c["decision"]["engine_amount"]) == c["opening"]
    assert float(c["proposal"]["fee_total_local"]) == 1_900_000 == sum(float(a) for a, _, _ in lines)
    assert all(n == "Director's figure: round figure agreed with the client" for _, _, n in lines)
    assert sum(float(x) for _, x, _ in lines) == c["opening"]  # the engine's figure is kept beside it
    assert {a["field"] for a in snapshot["_assumptions"]} >= {"gfa_m2", "fitout_area_m2"}


def test_a_rejection_is_recorded_without_a_proposal(demo_org, made, director):
    e = replied_tower(demo_org, made)
    with workspace(demo_org):
        c = feecard.decide(e, feecard.FeeDecision(decision="reject", reason="Client budget far below our fee"), director)
    assert c["decision"]["decision"] == "reject" and c["proposal"] is None


def test_the_proposal_shows_and_exports_as_either_template(demo_org, made, director):
    import io
    import pymupdf
    from docx import Document
    from bidwright.storage import blob_store
    e = replied_tower(demo_org, made)
    with workspace(demo_org):
        feecard.decide(e, feecard.FeeDecision(decision="approve"), director)
        with engine.connect() as conn:
            view = feecard.proposal_view(conn, e)
        p = view["proposal"]
        assert [t["key"] for t in view["templates"]] == ["full", "letter"]
        assert set(p["pending"]) == {"understanding", "scope", "exclusions", "terms"}
        with pytest.raises(HTTPException) as early:  # prose not written yet: say what is left
            feecard.export_proposal(e, feecard.ExportRequest(template="letter"), director)
        assert early.value.status_code == 409 and early.value.detail["problems"]
        with engine.begin() as conn:  # stand-in for drafting (a model call): write the prose by hand
            conn.execute(text("""UPDATE proposal.section SET body = 'Written for the test.', origin = 'estimator'
                                 WHERE version_id = :v AND kind = 'prose'"""), dict(v=p["version_id"]))
        letter = feecard.export_proposal(e, feecard.ExportRequest(template="letter"), director)["proposal"]
        full = feecard.export_proposal(e, feecard.ExportRequest(template="full"), director)["proposal"]
        with engine.connect() as conn:
            uris = dict(conn.execute(text("SELECT file_name, blob_uri FROM proposal.output WHERE version_id = :v"),
                                     dict(v=p["version_id"])).all())
    assert letter["status"] == "approved" and len(full["outputs_by_template"]["full"]) == 2
    pdf_name = next(o["file_name"] for o in full["outputs_by_template"]["letter"] if o["format"] == "pdf")
    assert pdf_name.endswith("_fee-letter.pdf")
    text_ = "".join(pg.get_text() for pg in pymupdf.open(stream=blob_store.get(uris[pdf_name]), filetype="pdf"))
    assert "Fee proposal:" in text_ and f"{full['fee_total_local']:,.0f}" in text_ and "Payment" in text_
    docx_name = next(o["file_name"] for o in full["outputs_by_template"]["full"] if o["format"] == "docx")
    headings = [x.text for x in Document(io.BytesIO(blob_store.get(uris[docx_name]))).paragraphs if x.style.name == "Heading 1"]
    assert len(headings) == 9  # the full proposal keeps its nine sections
