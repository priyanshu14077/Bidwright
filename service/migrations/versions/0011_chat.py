"""The chat assistant: conversations, their messages, and the actions it proposes.

- chat.thread: one conversation. On the Ask screen it starts without a request; it is tied to a request once
  one is pasted in, or when it is opened from a request's screen.
- chat.message: what was said, in order: the person's text, the assistant's answer with the sources it cites
  and any cards (a request summary, a proposed action).
- chat.pending_action: something the assistant proposed that changes a record or a gate (qualify, decline,
  add the client's reply, approve or override a fee, export). Nothing happens until a person confirms it; the
  confirmation runs with that person's own permissions and is logged like the same action on screen.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-20
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

ORG = "org_id uuid NOT NULL DEFAULT tenancy.current_org() REFERENCES tenancy.organization ON DELETE CASCADE"
TABLES = ["chat.thread", "chat.message", "chat.pending_action"]


def upgrade() -> None:
    op.execute(f"""
        CREATE SCHEMA chat;
        CREATE TABLE chat.thread (
          thread_id bigserial PRIMARY KEY,
          {ORG},
          envelope_id bigint REFERENCES intake.envelope ON DELETE SET NULL,
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE chat.message (
          message_id bigserial PRIMARY KEY,
          {ORG},
          thread_id bigint NOT NULL REFERENCES chat.thread ON DELETE CASCADE,
          role text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
          content jsonb NOT NULL,
          created_by text,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX message_by_thread ON chat.message (thread_id, message_id);
        CREATE TABLE chat.pending_action (
          action_id bigserial PRIMARY KEY,
          {ORG},
          thread_id bigint NOT NULL REFERENCES chat.thread ON DELETE CASCADE,
          envelope_id bigint REFERENCES intake.envelope ON DELETE CASCADE,
          kind text NOT NULL CHECK (kind IN ('add_reply', 'qualify', 'ask', 'decline', 'approve_fee', 'override_fee',
                                             'reject_fee', 'export_proposal')),
          payload jsonb NOT NULL,
          summary text NOT NULL,
          status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'cancelled', 'failed')),
          result jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          decided_by text,
          decided_at timestamptz
        );
    """)
    for table in TABLES:
        op.execute(f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY workspace ON {table}
              USING (org_id = tenancy.current_org()) WITH CHECK (org_id = tenancy.current_org());
        """)
    op.execute("""
        GRANT USAGE ON SCHEMA chat TO bidwright_app, bidwright_studio;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA chat TO bidwright_app, bidwright_studio;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA chat TO bidwright_app, bidwright_studio;
    """)


def downgrade() -> None:
    op.execute("DROP SCHEMA chat CASCADE")
