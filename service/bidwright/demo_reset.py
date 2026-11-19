"""Put the demo workspace back to a clean, known state before recording.

    make demo-reset            # lists what goes, asks, then reloads
    make demo-reset ARGS=--yes # no question

Clears every conversation and every request except the gold and evaluation sets (their own commands manage them),
then adds three sample requests from docs/requests and reads them with the model (a few cents):

  Wadi Hanifa Offices   email, brief and area schedule   ready to qualify
  Al Mouj villas        email without floor areas        waiting for the client (clarify)
  Corniche offices      email, deadline in one day       decline suggested

The Lusail WhatsApp is left out on purpose: it is the request to paste live, with its reply in followups/.
Pricing settings, the archive and members are not touched.
"""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Connection, text

from bidwright.config import REPO_ROOT
from bidwright.db import engine, owner_engine, workspace
from bidwright.extraction import extract_envelope
from bidwright.intake.ingest import delete_envelope, ingest

SAMPLES = REPO_ROOT / "docs" / "requests"
CURATED = [  # folder, channel
    ("1-complete-scope/A - Email with brief and area schedule", "email"),
    ("2-partial-scope/B - Email, missing areas", "email"),
    ("3-thin-or-out-of-scope/D - Email, deadline too close", "email"),
]
ARRIVED = datetime(2026, 11, 20, 9, 30, tzinfo=timezone(timedelta(hours=4)))  # the samples' own day
TEST_MATERIAL = "title LIKE '[gold %' OR title LIKE '[eval %'"


def inventory(conn: Connection) -> dict:
    return {
        "requests": conn.execute(text(f"SELECT envelope_id, coalesce(title, '(untitled)') FROM intake.envelope "
                                      f"WHERE title IS NULL OR NOT ({TEST_MATERIAL}) ORDER BY envelope_id")).all(),
        "chats": conn.execute(text("SELECT count(*) FROM chat.thread")).scalar(),
    }


def clear(conn: Connection) -> dict:
    """Every conversation, then every request that is not test material (with its readings, decisions and
    proposals). Runs as the owner: removing a request switches off two append-only triggers for a moment."""
    chats = conn.execute(text("DELETE FROM chat.thread")).rowcount
    ids = conn.execute(text(f"SELECT envelope_id FROM intake.envelope WHERE title IS NULL OR NOT ({TEST_MATERIAL})")).scalars().all()
    for e in ids:
        delete_envelope(conn, e)
    return {"chats": chats, "requests": len(ids)}


def load(conn: Connection, curated=CURATED, arrived: datetime = ARRIVED) -> list[int]:
    out = []
    for folder, channel in curated:
        files = [(p.name, p.read_bytes()) for p in sorted((SAMPLES / folder).iterdir()) if p.is_file()]
        e, _ = ingest(conn, files, channel, received_at=arrived)
        out.append(e)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", default="northbeam")
    parser.add_argument("--yes", action="store_true", help="do not ask before clearing")
    parser.add_argument("--no-read", action="store_true", help="add the samples without reading them (no model calls)")
    args = parser.parse_args()
    with engine.connect() as conn:
        org = conn.execute(text("SELECT org_id FROM tenancy.organization WHERE slug = :s"), dict(s=args.workspace)).scalar()
    if org is None:
        raise SystemExit(f"no workspace '{args.workspace}'")
    with workspace(org):
        with engine.connect() as conn:
            inv = inventory(conn)
        print(f"Clearing {inv['chats']} conversations and {len(inv['requests'])} requests in '{args.workspace}':")
        for e, title in inv["requests"]:
            print(f"  {e}  {title}")
        if not args.yes and input("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("left as it was")
        with owner_engine.begin() as conn:
            gone = clear(conn)
            ids = load(conn)
        print(f"cleared {gone['chats']} conversations and {gone['requests']} requests; added {len(ids)} sample requests")
        if not args.no_read:
            for e in ids:
                print(f"  reading request {e}…", flush=True)
                extract_envelope(e)
        with engine.connect() as conn:
            for e, title, lead in conn.execute(text("SELECT envelope_id, title, lead_status FROM intake.envelope "
                                                    "WHERE envelope_id = ANY(:i) ORDER BY envelope_id"), dict(i=ids)):
                print(f"  {e}  {title}  ({lead or 'not read'})")
    print("Ready. Live in the demo: paste docs/requests/2-partial-scope/A …/WhatsApp Chat - Lusail offices.txt, "
          "then its reply from followups/. See docs/demo-script.md.")


if __name__ == "__main__":
    main()
