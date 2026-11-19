"""Gold cases that are incoming requests rather than past proposals (the v1-requests set).

- A gold case no longer needs an archive proposal. It records its kind (WhatsApp one-liner, feasibility
  ask, ...), channel, receiving studio and what the request should lead to (recommendation, rules,
  flags, the fields follow-up questions must cover).
- Each expected field also has a status: found, ambiguous, missing, or referenced (taken from a named
  past project).
- Requests can arrive by WhatsApp or as call notes, not only by email or upload.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

OLD_CHANNELS = "'email', 'sharepoint', 'upload'"
NEW_CHANNELS = "'email', 'sharepoint', 'upload', 'whatsapp', 'notes'"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE eval.gold_case ALTER COLUMN proposal_id DROP NOT NULL;
        ALTER TABLE eval.gold_case ADD COLUMN kind text, ADD COLUMN channel text, ADD COLUMN studio text,
                                   ADD COLUMN expected jsonb NOT NULL DEFAULT '{{}}';
        ALTER TABLE eval.gold_truth ADD COLUMN status text
            CHECK (status IN ('found', 'ambiguous', 'missing', 'referenced')),
            ADD COLUMN hint text;

        ALTER TABLE intake.envelope DROP CONSTRAINT envelope_channel_check;
        ALTER TABLE intake.envelope ADD CONSTRAINT envelope_channel_check CHECK (channel IN ({NEW_CHANNELS}));
        ALTER TABLE lineage.source_document DROP CONSTRAINT source_document_channel_check;
        ALTER TABLE lineage.source_document ADD CONSTRAINT source_document_channel_check
            CHECK (channel IN ({NEW_CHANNELS}, 'archive'));
    """)


def downgrade() -> None:
    op.execute(f"""
        DELETE FROM eval.gold_case WHERE proposal_id IS NULL;
        ALTER TABLE eval.gold_truth DROP COLUMN status, DROP COLUMN hint;
        ALTER TABLE eval.gold_case DROP COLUMN kind, DROP COLUMN channel, DROP COLUMN studio, DROP COLUMN expected;
        ALTER TABLE eval.gold_case ALTER COLUMN proposal_id SET NOT NULL;

        ALTER TABLE intake.envelope DROP CONSTRAINT envelope_channel_check;
        ALTER TABLE intake.envelope ADD CONSTRAINT envelope_channel_check CHECK (channel IN ({OLD_CHANNELS}));
        ALTER TABLE lineage.source_document DROP CONSTRAINT source_document_channel_check;
        ALTER TABLE lineage.source_document ADD CONSTRAINT source_document_channel_check
            CHECK (channel IN ({OLD_CHANNELS}, 'archive'));
    """)
