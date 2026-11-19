"""Chat history: who each conversation belongs to, what it is called, and which requests it touched.

- chat.thread gains its owner (user_id, a real reference to tenancy.app_user rather than an email), a title
  (from the first thing asked, or the request it read), updated_at (the last message, for "recent first"),
  and archived_at (hidden from the list, never deleted: the decisions made in it stay auditable).
- chat.thread_request: the requests a conversation touched, and how. A conversation on the Ask screen can read
  several requests, open one, and decide on another; thread.envelope_id only says which request it is about
  now. relation is one of:
    opened_from  the conversation was started on that request's screen
    created      the conversation read a pasted message into that request
    discussed    a tool looked at it (what's missing, fee card, similar projects)
    decided      a decision proposed in the conversation was confirmed on it
  This is how a request shows the conversations about it, and a conversation shows its requests.

Existing conversations are back-filled: owners from created_by, titles from their first message, updated_at
from their last message, and thread_request from thread.envelope_id, cards and pending actions.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-20
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE chat.thread
          ADD COLUMN user_id uuid REFERENCES tenancy.app_user ON DELETE SET NULL,
          ADD COLUMN title text,
          ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
          ADD COLUMN archived_at timestamptz;

        CREATE INDEX thread_recent ON chat.thread (org_id, user_id, updated_at DESC) WHERE archived_at IS NULL;

        CREATE TABLE chat.thread_request (
          org_id uuid NOT NULL DEFAULT tenancy.current_org() REFERENCES tenancy.organization ON DELETE CASCADE,
          thread_id bigint NOT NULL REFERENCES chat.thread ON DELETE CASCADE,
          envelope_id bigint NOT NULL REFERENCES intake.envelope ON DELETE CASCADE,
          relation text NOT NULL CHECK (relation IN ('opened_from', 'created', 'discussed', 'decided')),
          first_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (thread_id, envelope_id, relation)
        );
        CREATE INDEX thread_request_by_envelope ON chat.thread_request (envelope_id, thread_id);

        ALTER TABLE chat.thread_request ENABLE ROW LEVEL SECURITY;
        ALTER TABLE chat.thread_request FORCE ROW LEVEL SECURITY;
        CREATE POLICY workspace ON chat.thread_request
          USING (org_id = tenancy.current_org()) WITH CHECK (org_id = tenancy.current_org());
        GRANT SELECT, INSERT, UPDATE, DELETE ON chat.thread_request TO bidwright_app, bidwright_studio;

    """)
    # Row-level security hides every row unless app.org_id names a workspace, so back-fill one workspace at a time.
    op.execute("""
        DO $$
        DECLARE org uuid;
        BEGIN
          FOR org IN SELECT org_id FROM tenancy.organization LOOP
            PERFORM set_config('app.org_id', org::text, true);
            UPDATE chat.thread t SET user_id = u.user_id FROM tenancy.app_user u WHERE u.email = t.created_by;
            UPDATE chat.thread t SET
              updated_at = coalesce((SELECT max(created_at) FROM chat.message m WHERE m.thread_id = t.thread_id), t.created_at),
              title = (SELECT left(regexp_replace(m.content->>'text', '\\s+', ' ', 'g'), 80) FROM chat.message m
                       WHERE m.thread_id = t.thread_id AND m.role = 'user' ORDER BY m.message_id LIMIT 1);

            INSERT INTO chat.thread_request (org_id, thread_id, envelope_id, relation, first_at)
            SELECT org_id, thread_id, envelope_id, 'opened_from', created_at FROM chat.thread WHERE envelope_id IS NOT NULL
            ON CONFLICT DO NOTHING;
            INSERT INTO chat.thread_request (org_id, thread_id, envelope_id, relation, first_at)
            SELECT m.org_id, m.thread_id, (c->>'envelope_id')::bigint, 'discussed', min(m.created_at)
            FROM chat.message m, jsonb_array_elements(coalesce(m.content->'cards', '[]'::jsonb)) c
            WHERE c->>'envelope_id' ~ '^[0-9]+$'
              AND EXISTS (SELECT 1 FROM intake.envelope e WHERE e.envelope_id = (c->>'envelope_id')::bigint)
            GROUP BY 1, 2, 3 ON CONFLICT DO NOTHING;
            INSERT INTO chat.thread_request (org_id, thread_id, envelope_id, relation, first_at)
            SELECT org_id, thread_id, envelope_id, 'decided', min(decided_at) FROM chat.pending_action
            WHERE status = 'confirmed' AND envelope_id IS NOT NULL GROUP BY 1, 2, 3
            ON CONFLICT DO NOTHING;
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE chat.thread_request;
        DROP INDEX chat.thread_recent;
        ALTER TABLE chat.thread DROP COLUMN user_id, DROP COLUMN title, DROP COLUMN updated_at, DROP COLUMN archived_at;
    """)
