"""Workspace isolation, enforced by row-level security. Needs the database up and the seed run."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from bidwright import tenancy
from bidwright.api import app
from bidwright.db import engine, workspace


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


def test_no_workspace_sees_nothing():
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM archive.proposal")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM intake.envelope")).scalar() == 0


def test_no_workspace_cannot_insert():
    with pytest.raises(DBAPIError), engine.begin() as conn:
        conn.execute(text("INSERT INTO ref.studio (code, name, city, country) VALUES ('X', 'X', 'X', 'AE')"))


def test_workspace_cannot_write_into_another(demo_org, stranger):
    _, other = stranger
    with pytest.raises(DBAPIError), workspace(demo_org), engine.begin() as conn:
        conn.execute(text("INSERT INTO ref.studio (org_id, code, name, city, country) VALUES (:o, 'X', 'X', 'X', 'AE')"),
                     dict(o=other))


def test_new_workspace_gets_starter_pack_but_not_demo_archive(demo_org, stranger):
    client, other = stranger
    with workspace(other), engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM archive.proposal")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM ref.term_synonym")).scalar() > 0
    assert client.get("/api/envelopes").json() == []
    assert client.get("/api/reference").json()["archive"] == []


def test_api_hides_other_workspace_records(demo_org, stranger):
    client, _ = stranger
    with workspace(demo_org), engine.connect() as conn:
        envelope_id = conn.execute(text("SELECT min(envelope_id) FROM intake.envelope")).scalar()
        document_id = conn.execute(text("SELECT min(document_id) FROM lineage.source_document")).scalar()
    assert client.get(f"/api/envelopes/{envelope_id}").status_code == 404
    assert client.get(f"/api/documents/{document_id}/pdf").status_code == 404


def test_viewer_cannot_change_records(demo_org):
    email = f"viewer-{uuid.uuid4().hex[:8]}@example.com"
    with engine.begin() as conn:
        user_id = tenancy.create_user(conn, email, "Viewer", "viewer password 1")
        tenancy.add_member(conn, demo_org, user_id, "viewer")
    try:
        client = TestClient(app)
        assert client.post("/api/auth/login", json={"email": email, "password": "viewer password 1"}).status_code == 200
        assert client.get("/api/envelopes").status_code == 200
        r = client.post("/api/envelopes/1/fields/project_name", json={"action": "edit", "reason": "test", "value": "x"})
        assert r.status_code == 403
        assert client.post("/api/reference/synonyms", json={"synonym": "x", "target_table": "stage",
                                                             "target_code": "feasibility"}).status_code == 403
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tenancy.app_user WHERE user_id = :u"), dict(u=user_id))


def test_signed_out_is_refused():
    assert TestClient(app).get("/api/envelopes").status_code == 401
