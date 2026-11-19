import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bidwright.api import app
from bidwright.db import engine


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

