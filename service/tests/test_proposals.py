"""Proposals: create, edit, approve (gate 3), version. Runs in a scratch workspace; needs the database up."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from bidwright import tenancy
from bidwright.api import app
from bidwright.db import engine, workspace
from conftest import RECORD, confirm

PROSE = ("understanding", "scope", "exclusions", "terms")


def create(client, envelope_id, **choices):
    r = client.post(f"/api/envelopes/{envelope_id}/proposals", json=choices)
    assert r.status_code == 200, r.text
    return r.json()


def write_prose(client, v) -> dict:
    for code in PROSE:
        v = client.patch(f"/api/proposals/{v['proposal_id']}/versions/{v['version_no']}/sections/{code}",
                         json={"revision": v["revision"], "body": f"{code} text"}).json()
    return v


def test_create_prices_and_lays_out_the_proposal(request_57):
    client, _, envelope_id = request_57
    v = create(client, envelope_id)
    assert v["status"] == "draft" and v["version_no"] == 1 and v["revision"] == 1
    assert v["reference"].endswith("-0001") and v["title"] == "Bay Square Offices"
    lines = v["fee_lines"]
    assert [(ln["service"], ln["stage"]) for ln in lines] == [
        ("architecture", "concept"), ("architecture", "schematic_design"),
        ("interior_design", "concept"), ("interior_design", "schematic_design")]
    assert float(v["fee_total_local"]) == sum(float(ln["amount_local"]) for ln in lines) == float(v["engine_total_local"])
    sections = {s["code"]: s for s in v["sections"]}
    assert list(sections) == ["cover", "understanding", "scope", "deliverables", "programme", "fees",
                              "reimbursables", "payment", "exclusions", "terms"]
    assert sections["cover"]["data"]["location"] == "Dubai, United Arab Emirates"
    assert all(sections[c]["origin"] == "pending" and not sections[c]["body"] for c in PROSE)
    assert sections["exclusions"]["data"]["excluded_scope"] == RECORD["excluded_scope"]
    assert sum(m["pct"] for m in sections["payment"]["data"]) == pytest.approx(100)
    assert [e["action"] for e in v["events"]] == ["create"]
    assert create(client, envelope_id)["reference"].endswith("-0002")


def test_editing_a_line_recomputes_and_logs(request_57):
    client, _, envelope_id = request_57
    v = create(client, envelope_id)
    url = f"/api/proposals/{v['proposal_id']}/versions/1"
    first = v["fee_lines"][0]
    r = client.patch(f"{url}/lines/1", json={"revision": 1, "amount_local": 100000, "reason": "client budget"})
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert v2["revision"] == 2
    line = v2["fee_lines"][0]
    assert float(line["amount_local"]) == 100000 and line["engine_amount_local"] == first["engine_amount_local"]
    assert float(v2["fee_total_local"]) == sum(float(ln["amount_local"]) for ln in v2["fee_lines"])
    payment = next(s["data"] for s in v2["sections"] if s["code"] == "payment")
    assert sum(m["pct"] for m in payment) == pytest.approx(100)
    assert sum(m["amount_local"] for m in payment) == pytest.approx(float(v2["fee_total_local"]), abs=5)
    event = v2["events"][-1]
    assert event["action"] == "edit_line" and event["reason"] == "client budget"

    stale = client.patch(f"{url}/lines/2", json={"revision": 1, "amount_local": 5})  # someone else's edit won
    assert stale.status_code == 409 and stale.json()["detail"]["current_revision"] == 2
    assert client.patch(f"{url}/sections/payment", json={"revision": 2, "data": []}).status_code == 422


def test_approval_gate_freeze_and_new_version(request_57):
    client, org, envelope_id = request_57
    v = create(client, envelope_id)
    pid, url = v["proposal_id"], f"/api/proposals/{v['proposal_id']}/versions"

    blocked = client.post(f"{url}/1/approve", json={"revision": v["revision"]})
    assert blocked.status_code == 409 and "Project Understanding" in blocked.json()["detail"]["problems"][0]

    v = write_prose(client, v)
    assert client.post(f"{url}/1/approve", json={"revision": v["revision"]}).status_code == 200
    v1 = client.get(f"{url}/1").json()
    assert v1["status"] == "approved" and v1["approved_at"]

    # frozen, through the API and in the database itself
    assert client.patch(f"{url}/1/lines/1", json={"revision": v1["revision"], "amount_local": 1}).status_code == 409
    with pytest.raises(DBAPIError, match="cannot be changed"), workspace(org), engine.begin() as conn:
        conn.execute(text("UPDATE proposal.line SET amount_local = 1 WHERE version_id = :v"),
                     dict(v=v1["version_id"]))
    with pytest.raises(DBAPIError, match="cannot be deleted"), workspace(org), engine.begin() as conn:
        conn.execute(text("DELETE FROM proposal.version WHERE version_id = :v"), dict(v=v1["version_id"]))

    v2 = client.post(url, json={"reason": "client asked for a lower fee"}).json()
    assert v2["version_no"] == 2 and v2["status"] == "draft" and v2["based_on"] == v1["version_id"]
    assert [ln["amount_local"] for ln in v2["fee_lines"]] == [ln["amount_local"] for ln in v1["fee_lines"]]
    assert client.post(url, json={}).status_code == 409  # one draft at a time
    v2 = client.patch(f"{url}/2/lines/1", json={"revision": v2["revision"], "amount_local": 1000}).json()
    assert client.get(f"{url}/1").json()["fee_lines"] == v1["fee_lines"]

    assert client.post(f"{url}/2/approve", json={"revision": v2["revision"]}).status_code == 200
    assert [x["status"] for x in client.get(f"/api/proposals/{pid}").json()["versions"]] == ["superseded", "approved"]


def test_reconfirmed_request_must_be_repriced(request_57):
    client, org, envelope_id = request_57
    v = write_prose(client, create(client, envelope_id))
    confirm(org, envelope_id, dict(RECORD, gfa_m2=30000), 2)
    url = f"/api/proposals/{v['proposal_id']}/versions/1"
    assert client.get(url).json()["stale"] is True
    r = client.post(f"{url}/approve", json={"revision": v["revision"]})
    assert r.status_code == 409 and "reprice" in r.json()["detail"]["problems"][0]

    r = client.post(f"{url}/reprice", json={"revision": v["revision"], "complexity": "high"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["record_version"] == 2 and not v["stale"] and v["pricing_choices"] == {"complexity": "high"}
    assert all(s["body"] == f"{s['code']} text" for s in v["sections"] if s["code"] in PROSE)  # hand edits kept
    assert float(v["fee_total_local"]) > 0
    assert client.post(f"{url}/approve", json={"revision": v["revision"]}).status_code == 200


def test_estimator_edits_but_cannot_approve(request_57):
    client, org, envelope_id = request_57
    v = write_prose(client, create(client, envelope_id))
    email = f"estimator-{uuid.uuid4().hex[:8]}@example.com"
    with engine.begin() as conn:
        user_id = tenancy.create_user(conn, email, "Estimator", "estimator password 1")
        tenancy.add_member(conn, org, user_id, "estimator")
    try:
        est = TestClient(app)
        assert est.post("/api/auth/login", json={"email": email, "password": "estimator password 1"}).status_code == 200
        url = f"/api/proposals/{v['proposal_id']}/versions/1"
        assert est.patch(f"{url}/lines/1", json={"revision": v["revision"], "amount_local": 5}).status_code == 200
        assert est.post(f"{url}/approve", json={"revision": v["revision"] + 1}).status_code == 403
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tenancy.app_user WHERE user_id = :u"), dict(u=user_id))


def test_other_workspaces_cannot_see_it(request_57):
    client, _, envelope_id = request_57
    v = create(client, envelope_id)
    demo = TestClient(app)
    assert demo.post("/api/auth/login", json={"email": "demo@northbeam.example",
                                              "password": "northbeam-demo"}).status_code == 200
    assert demo.get(f"/api/proposals/{v['proposal_id']}").status_code == 404
    assert all(p["proposal_id"] != v["proposal_id"] for p in demo.get("/api/proposals").json())
