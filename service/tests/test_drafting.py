"""Drafting: the checks on Claude's answer, assembly with the standard text, and the endpoint.
The model is replaced by a fake here; `make draft-smoke` runs the real one."""

import hashlib
import json

import pytest
from sqlalchemy import text

from bidwright import drafting, llm
from bidwright.db import engine, workspace
from conftest import RECORD, confirm

TERMS_LINES = [  # a client's conditions, as the reader stores a PDF page
    {"id": "p1:1", "text": "Conditions of Appointment", "bbox": [60, 50, 300, 70]},
    {"id": "p1:2", "text": "Copyright in all designs shall transfer to the Client upon full payment.",
     "bbox": [60, 80, 500, 95]},
    {"id": "p1:3", "text": "The Consultant shall provide a 3D BIM model to LOD 300 at Schematic Design.",
     "bbox": [60, 100, 500, 115]},
    {"id": "p1:4", "text": "Built-Up Area approximately 31,050 m2.", "bbox": [60, 120, 500, 135]},
]


def sources(**over) -> drafting.Sources:
    base = dict(
        firm="Test Practice", record=dict(RECORD), services=["architecture", "interior_design"],
        stages=["concept", "schematic_design"],
        documents={"doc9": {"file_name": "Client_Terms.pdf", "lines": {ln["id"]: ln["text"] for ln in TERMS_LINES},
                            "rendered": ""}},
        past=[{"reference": "NBS-DXB-2025-004", "title": "Coral Bay Resort & Spa - 220-key beachfront resort",
               "client": "Coral Bay Hospitality Holdings", "sections": {}}],
        scope_text={("architecture", "concept"): "Concept scope.", ("architecture", "schematic_design"): "SD scope.",
                    ("interior_design", "concept"): "Interior concept."},
        exclusions=["Structural, MEP and civil engineering design", "Physical models"],
        terms={"standard": "Subject to {firm}'s terms.", "signoff": "For and on behalf of {firm}"},
        positions={"copyright": ("Copyright", "Retained by {firm}"),
                   "liability_cap": ("Liability cap", "Cap at 100% of fee"),
                   "payment_terms": ("Payment terms", "{payment_days} days from invoice")},
        stage_labels={"concept": "Concept Design", "schematic_design": "Schematic Design"},
        service_labels={"architecture": "Architecture", "interior_design": "Interior Design"}, payment_days=30)
    return drafting.Sources(**(base | over))


GOOD = {
    "understanding": [{"text": "Marina Crest Developments L.L.C. has invited Test Practice to propose for Bay Square "
                               "Offices in Dubai, an office building of 27,000 m² on a 3,600 m² site.",
                       "sources": ["record.client_name", "record.gfa_m2", "record.site_area_m2"]}],
    "scope_additions": [{"service": "architecture", "text": "A 3D BIM model to LOD 300 at Schematic Design.",
                         "sources": ["doc9/p1:3"]}],
    "amendments": [{"clause": "copyright", "label": "", "client_request": "Copyright transfers to the client on "
                                                                          "full payment", "sources": ["doc9/p1:2"]}],
    "notes": ["The RFP gives 31,050 m² BUA; the record has 27,000 m²."],
}


def bad(**over) -> dict:
    return json.loads(json.dumps(GOOD)) | over


def test_a_grounded_draft_passes():
    drafting.validator(sources())(GOOD)


@pytest.mark.parametrize("answer, problem", [
    (bad(understanding=[{"text": "A 45,000 m² tower.", "sources": ["record.gfa_m2"]}]), "neither the record"),
    (bad(understanding=[{"text": "An office building.", "sources": ["doc9/p9:9"]}]), "not given"),
    (bad(understanding=[{"text": "An office building.", "sources": []}]), "no sources"),
    (bad(understanding=[{"text": "Like Coral Bay Resort & Spa, an office.", "sources": ["record.typology"]}]),
     "another project"),
    (bad(amendments=[{"clause": "copyright", "label": "", "client_request": "x", "sources": ["record.city"]}]),
     "RFP line"),
    (bad(understanding=[]), "1 to 3"),
])
def test_the_checks_reject(answer, problem):
    with pytest.raises(llm.ValidationFailed, match=problem):
        drafting.validator(sources())(answer)


def test_a_figure_from_the_rfp_is_allowed_even_if_the_record_differs():
    drafting.validator(sources())(bad(understanding=[{"text": "The RFP states 31,050 m².", "sources": ["doc9/p1:4"]}]))


def test_scope_is_standard_text_plus_cited_additions():
    d = drafting.scope(sources(), GOOD["scope_additions"])
    assert "### 2.1 Architecture" in d.body and "**Concept Design:** Concept scope." in d.body
    assert "### 2.3 Project-specific requirements" in d.body and "LOD 300" in d.body
    assert d.warnings == ["no standard scope text for interior_design at Schematic Design"]
    assert drafting.TO_BE_WRITTEN in d.body
    assert d.citations[0]["sources"][0]["quote"].startswith("The Consultant shall provide")


def test_exclusions_merge_the_record_without_duplicates():
    rec = dict(RECORD, excluded_scope=["MEP / structural engineering", "Traffic impact assessment"])
    d = drafting.exclusions(sources(record=rec))
    assert d.data["items"] == ["Structural, MEP and civil engineering design", "Physical models",
                               "Traffic impact assessment"]
    assert d.data["added_from_record"] == ["Traffic impact assessment"]


def test_terms_take_stated_deviations_from_the_record_and_the_rest_from_the_model():
    rec = dict(RECORD, liability_cap="uncapped", payment_terms_days=90)
    model = GOOD["amendments"] + [{"clause": "liability_cap", "label": "", "client_request": "unlimited",
                                   "sources": ["doc9/p1:2"]}]
    d = drafting.terms(sources(record=rec), model)
    assert [(a["label"], a["client_request"], a["position"]) for a in d.data["amendments"]] == [
        ("Liability cap", "Uncapped liability", "Cap at 100% of fee"),
        ("Payment terms", "90 days from invoice", "30 days from invoice"),
        ("Copyright", "Copyright transfers to the client on full payment", "Retained by Test Practice")]
    assert d.body.startswith("Subject to Test Practice's terms.") and "| Test Practice position |" in d.body


# ---------------------------------------------------------------- endpoint, with a fake model

@pytest.fixture
def proposal(request_57):
    client, org, envelope_id = request_57
    with workspace(org), engine.begin() as conn:
        doc = conn.execute(text("""INSERT INTO lineage.source_document (envelope_id, channel, file_name, sha256,
                                     blob_uri) VALUES (:e, 'upload', 'Client_Terms.pdf', :h, 'test://none')
                                   RETURNING document_id"""),
                           dict(e=envelope_id, h=hashlib.sha256(str(envelope_id).encode()).hexdigest())).scalar_one()
        conn.execute(text("INSERT INTO lineage.page (document_id, page_no, width, height, lines) "
                          "VALUES (:d, 1, 595, 842, CAST(:l AS jsonb))"), dict(d=doc, l=json.dumps(TERMS_LINES)))
    v = client.post(f"/api/envelopes/{envelope_id}/proposals", json={}).json()
    return client, org, v, f"doc{doc}"


def fake_model(answer: dict, calls: list, side_effect=None):
    def call(src, trace_id):
        calls.append(src)
        if side_effect:
            side_effect()
        drafting.validator(src)(answer)
        return llm.Result(answer, call_id=0, attempts=1)
    return call


def answer_for(doc: str) -> dict:
    return json.loads(json.dumps(GOOD).replace("doc9/", f"{doc}/"))


def test_draft_fills_the_pending_sections_and_unblocks_approval(proposal, monkeypatch):
    client, _, v, doc = proposal
    calls = []
    monkeypatch.setattr(drafting, "call_model", fake_model(answer_for(doc), calls))
    monkeypatch.setattr(drafting.settings, "anthropic_api_key", "test")
    url = f"/api/proposals/{v['proposal_id']}/versions/1"
    r = client.post(f"{url}/draft", json={"revision": v["revision"]})
    assert r.status_code == 200, r.text
    out = r.json()
    sections = {s["code"]: s for s in out["sections"]}
    assert {sections[c]["origin"] for c in drafting.PROSE} == {"claude", "library"}
    assert "Bay Square Offices" in sections["understanding"]["body"]
    assert "| Gross floor area (GFA) | 27,000 m² |" in sections["understanding"]["body"]
    assert sections["terms"]["data"]["amendments"][0]["label"] == "Copyright"
    cited = sections["terms"]["citations"][0]["sources"][0]
    assert cited["document"] == "Client_Terms.pdf" and cited["page"] == 1 and cited["rects"] == [[60, 80, 500, 95]]
    assert len(calls) == 1 and calls[0].documents[doc]["lines"]["p1:2"].startswith("Copyright")
    assert out["draft"]["notes"] == GOOD["notes"] and out["events"][-1]["action"] == "draft"

    # the starter library has interior scope for both stages, so nothing is marked [to be written]
    assert client.post(f"{url}/approve", json={"revision": out["revision"]}).status_code == 200


def test_an_edit_during_drafting_wins_and_nothing_is_written(proposal, monkeypatch):
    client, org, v, doc = proposal

    def someone_edits():
        with workspace(org), engine.begin() as conn:
            conn.execute(text("UPDATE proposal.version SET revision = revision + 1 WHERE version_id = :v"),
                         dict(v=v["version_id"]))

    monkeypatch.setattr(drafting, "call_model", fake_model(answer_for(doc), [], someone_edits))
    monkeypatch.setattr(drafting.settings, "anthropic_api_key", "test")
    url = f"/api/proposals/{v['proposal_id']}/versions/1"
    assert client.post(f"{url}/draft", json={"revision": v["revision"]}).status_code == 409
    after = client.get(url).json()
    assert all(s["origin"] == "pending" for s in after["sections"] if s["code"] in drafting.PROSE)


def test_exclusions_alone_need_no_model_and_no_key(proposal, monkeypatch):
    client, _, v, _ = proposal
    monkeypatch.setattr(drafting, "call_model", lambda *a: pytest.fail("the model was called"))
    monkeypatch.setattr(drafting.settings, "anthropic_api_key", "")
    url = f"/api/proposals/{v['proposal_id']}/versions/1"
    assert client.post(f"{url}/draft", json={"revision": v["revision"], "sections": ["understanding"]}).status_code == 503
    r = client.post(f"{url}/draft", json={"revision": v["revision"], "sections": ["exclusions"]})
    assert r.status_code == 200, r.text
    assert next(s for s in r.json()["sections"] if s["code"] == "exclusions")["origin"] == "library"
