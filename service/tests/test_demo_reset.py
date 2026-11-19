"""make demo-reset clears conversations and requests but never the gold and evaluation sets."""
from sqlalchemy import text

from bidwright import demo_reset
from bidwright.db import engine, owner_engine, workspace
from test_gate1 import new_request


def test_reset_clears_chats_and_requests_and_keeps_test_material(stranger):
    client, org = stranger
    e, _ = new_request(org, "Hotel in Doha")
    with workspace(org), owner_engine.begin() as conn:
        gold = conn.execute(text("""INSERT INTO intake.envelope (title, channel, status, dataset_version)
                                    VALUES ('[gold P99]', 'upload', 'received', 'test') RETURNING envelope_id""")).scalar_one()
        conn.execute(text("INSERT INTO chat.thread (envelope_id, created_by) VALUES (:e, 'x@example.com')"), dict(e=e))
    with workspace(org), owner_engine.begin() as conn:
        assert [r[0] for r in demo_reset.inventory(conn)["requests"]] == [e]
        assert demo_reset.clear(conn) == {"chats": 1, "requests": 1}
        ids = demo_reset.load(conn)
    with workspace(org), engine.connect() as conn:
        left = dict(conn.execute(text("SELECT envelope_id, title FROM intake.envelope")).all())
        assert gold in left and e not in left and set(ids) <= set(left) and len(ids) == 3
        assert conn.execute(text("SELECT count(*) FROM chat.thread")).scalar() == 0
