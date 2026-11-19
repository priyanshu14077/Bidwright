"""Export: an approved version as DOCX and PDF. Runs LibreOffice, so it takes a few seconds."""

import io
import uuid

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import text

from bidwright import tenancy
from bidwright.api import app
from bidwright.db import engine, workspace
from bidwright.export import DEFAULT_TEMPLATE, markdown_subdoc
from docxtpl import DocxTemplate

PROSE = {
    "understanding": "Marina Crest has invited the practice to propose for **Bay Square Offices**.\n\n"
                     "| Item | Description |\n|---|---|\n| Gross floor area (GFA) | 27,000 m² |",
    "scope": "### 2.1 Architecture\n\n**Concept Design:** Concept scope.",
    "exclusions": "- Structural, MEP and civil engineering design\n- Physical models",
    "terms": "Subject to the practice's terms.\n\n#### Client-requested amendments\n\n| Clause | Client request | Position |\n"
             "|---|---|---|\n| Copyright | Transfers to the client | Retained |\n\nFor and on behalf of the practice",
}


@pytest.fixture
def approved(request_57):
    client, org, envelope_id = request_57
    v = client.post(f"/api/envelopes/{envelope_id}/proposals", json={}).json()
    url = f"/api/proposals/{v['proposal_id']}/versions/1"
    for code, body in PROSE.items():
        v = client.patch(f"{url}/sections/{code}", json={"revision": v["revision"], "body": body}).json()
    draft = v
    v = client.post(f"{url}/approve", json={"revision": v["revision"]}).json()
    assert v["status"] == "approved"
    return client, org, v, url, draft


def test_draft_versions_are_not_exported(request_57):
    client, _, envelope_id = request_57
    v = client.post(f"/api/envelopes/{envelope_id}/proposals", json={}).json()
    r = client.post(f"/api/proposals/{v['proposal_id']}/versions/1/export")
    assert r.status_code == 409 and "approve" in r.json()["detail"]


def test_export_makes_a_filled_docx_and_a_pdf(approved):
    client, org, v, url, _ = approved
    r = client.post(f"{url}/export")
    assert r.status_code == 200, r.text
    files = {o["format"]: o for o in r.json()["outputs"]}
    assert set(files) == {"docx", "pdf"} and files["docx"]["file_name"].startswith(v["reference"] + "_v1_Bay-Square")

    docx = client.get(f"/api/outputs/{files['docx']['output_id']}")
    assert docx.headers["content-disposition"].endswith('.docx"')
    doc = Document(io.BytesIO(docx.content))
    texts = [p.text for p in doc.paragraphs]
    everything = "\n".join(texts + [c.text for t in doc.tables for row in t.rows for c in row.cells]
                           + [p.text for p in doc.sections[0].header.paragraphs])
    assert "{{" not in everything and "{%" not in everything
    assert "Isolation Test Practice  ·  " + v["reference"] in everything  # running header
    assert "Prepared for: Marina Crest Developments L.L.C." in texts
    headings = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert headings[0] == "1. Project Understanding" and headings[-1] == "9. Terms and Conditions" and len(headings) == 9
    assert "2.1 Architecture" in [p.text for p in doc.paragraphs if p.style.name == "Heading 2"]
    assert [p.text for p in doc.paragraphs if p.style.name == "List Bullet"][-2:] == [
        "Structural, MEP and civil engineering design", "Physical models"]
    fee_table = next(t for t in doc.tables if t.rows[0].cells[0].text == "Service")
    total = f"{v['fee_total_local']:,.0f}"
    assert fee_table.rows[-1].cells[2].text == total and len(fee_table.rows) == len(v["fee_lines"]) + 2
    assert any(t.rows[0].cells[0].text == "Clause" for t in doc.tables)  # Markdown table from the terms
    bold = [r.text for p in doc.paragraphs for r in p.runs if r.bold]
    assert "Bay Square Offices" in bold and "Concept Design:" in bold

    pdf = client.get(f"/api/outputs/{files['pdf']['output_id']}").content
    assert pdf.startswith(b"%PDF")
    pdf_text = "".join(page.get_text() for page in pymupdf.open(stream=pdf, filetype="pdf"))
    assert v["reference"] in pdf_text and total in pdf_text and "Payment Schedule" in pdf_text

    again = client.post(f"{url}/export").json()
    assert again["reused"] is True and len(client.get(url).json()["outputs"]) == 2
    with workspace(org), engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM proposal.event WHERE action = 'export'")).scalar() == 1


def test_only_approvers_export(approved):
    _, org, v, url, _ = approved
    email = f"estimator-{uuid.uuid4().hex[:8]}@example.com"
    with engine.begin() as conn:
        user_id = tenancy.create_user(conn, email, "Estimator", "estimator password 1")
        tenancy.add_member(conn, org, user_id, "estimator")
    try:
        est = TestClient(app)
        est.post("/api/auth/login", json={"email": email, "password": "estimator password 1"})
        assert est.post(f"{url}/export").status_code == 403
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tenancy.app_user WHERE user_id = :u"), dict(u=user_id))


def test_markdown_becomes_word_styles():
    tpl = DocxTemplate(str(DEFAULT_TEMPLATE))
    sd = markdown_subdoc(tpl, "### Heading\n\n- one\n- **two**\n\nPlain\nnext line\n\n| A | B |\n|---|---|\n| 1 | 2 |")
    paragraphs = sd.subdocx.paragraphs
    assert [(p.style.name, p.text) for p in paragraphs[:3]] == [("Heading 2", "Heading"), ("List Bullet", "one"),
                                                                 ("List Bullet", "two")]
    assert paragraphs[2].runs[0].bold and paragraphs[3].text == "Plain\nnext line"
    assert [[c.text for c in r.cells] for r in sd.subdocx.tables[0].rows] == [["A", "B"], ["1", "2"]]
