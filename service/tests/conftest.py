import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bidwright.api import app
from bidwright.db import engine, workspace


@pytest.fixture(scope="session")
def demo_org():
    with engine.connect() as conn:
        org_id = conn.execute(text("SELECT org_id FROM tenancy.organization WHERE is_demo")).scalar()
    if org_id is None:
        pytest.skip("no demo workspace; run make seed")
    return org_id


@pytest.fixture
def stranger():
    """A second workspace with its own owner, removed afterwards."""
    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    client = TestClient(app)
    r = client.post("/api/auth/signup", json={"name": "Test Owner", "email": email, "password": "correct horse 42",
                                               "workspace": "Isolation Test Practice"})
    assert r.status_code == 200, r.text
    yield client, r.json()["org_id"]
    with engine.begin() as conn:  # workspace rows go with the organization (ON DELETE CASCADE)
        conn.execute(text("DELETE FROM tenancy.organization WHERE org_id = :o"), dict(o=r.json()["org_id"]))
        conn.execute(text("DELETE FROM tenancy.app_user WHERE email = :e"), dict(e=email))


RECORD = {  # the shape of a confirmed record, as the review screen writes it
    "project_name": "Bay Square Offices", "client_name": "Marina Crest Developments L.L.C.",
    "rfp_reference": "MCD-RFP-2026-89", "issue_date": "2026-02-11", "country": "AE", "city": "Dubai",
    "location_tier": "prime", "typology": "office", "services": ["architecture", "interior_design"],
    "stage_package": ["concept", "schematic_design"], "gfa_m2": 27000, "site_area_m2": 3600,
    "fitout_area_m2": 9000, "client_status": "repeat", "billing_currency": "AED", "payment_terms_days": 30,
    "excluded_scope": ["Structural, MEP and civil engineering"], "liability_cap": "capped",
}


def confirm(org, envelope_id: int, record: dict, version: int) -> None:
    with workspace(org), engine.begin() as conn:
        conn.execute(text("""INSERT INTO intake.opportunity (envelope_id, version, record, schema_version, confirmed_by)
                             VALUES (:e, :v, CAST(:r AS jsonb), 'request-v0', 'test')"""),
                     dict(e=envelope_id, v=version, r=json.dumps(record)))


@pytest.fixture
def request_57(stranger):
    """A confirmed request in a scratch workspace."""
    client, org = stranger
    with workspace(org), engine.begin() as conn:
        envelope_id = conn.execute(text("""INSERT INTO intake.envelope (title, channel, status, dataset_version)
                                           VALUES ('Bay Square', 'upload', 'confirmed', 'test')
                                           RETURNING envelope_id""")).scalar_one()
    confirm(org, envelope_id, RECORD, 1)
    return client, org, envelope_id
