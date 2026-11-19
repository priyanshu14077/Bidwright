"""Draft a real proposal with the real model: the demo's envelope 57 (Bay Square Offices).
Creates a proposal in the demo workspace, drafts it, prints the sections and the cost, then deletes it.

    make draft-smoke        (spends a few cents of API credit)
"""

import json
import sys

from fastapi.testclient import TestClient
from sqlalchemy import text

from bidwright.api import app
from bidwright.archive.loader import DEMO_USER
from bidwright.db import engine, workspace

ENVELOPE = int(sys.argv[1]) if len(sys.argv) > 1 else 57


def main() -> None:
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"email": DEMO_USER["email"],
                                                "password": DEMO_USER["password"]}).status_code == 200
    v = client.post(f"/api/envelopes/{ENVELOPE}/proposals", json={}).json()
    pid = v["proposal_id"]
    try:
        r = client.post(f"/api/proposals/{pid}/versions/1/draft", json={"revision": v["revision"]})
        out = r.json()
        if r.status_code != 200:
            print(r.status_code, json.dumps(out, indent=1))
            return
        for s in out["sections"]:
            if s["code"] in ("understanding", "scope", "exclusions", "terms"):
                print(f"\n===== {s['title']}  [{s['origin']}]\n{s['body']}")
                for c in s["citations"]:
                    print(f"   {c['part']}: " + "; ".join(f"{x['id']} = {str(x.get('quote'))[:70]}" for x in c["sources"]))
        print("\n===== notes for the estimator\n" + "\n".join(f"- {n}" for n in out["draft"]["notes"]))
        print("\nwarnings:", out["draft"]["warnings"])
        print("llm call:", json.dumps(out["draft"]["llm_call"], default=str), "attempts:", out["draft"]["attempts"])
    finally:
        with engine.connect() as conn:
            org = conn.execute(text("SELECT org_id FROM tenancy.organization WHERE is_demo")).scalar()
        with workspace(org), engine.begin() as conn:
            conn.execute(text("DELETE FROM proposal.bid WHERE proposal_id = :p"), dict(p=pid))


if __name__ == "__main__":
    main()
