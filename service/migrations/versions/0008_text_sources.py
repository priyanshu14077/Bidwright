"""Messages, notes and screenshots as sources.

- source_document.text_uri: the document's plain text, stored once. For a message or an email it is the text as
  received; for a screenshot or photo it is the transcription. Quotes are located in this text by character
  offset, so the app can show a pasted message as text with the quoted words highlighted.
- extraction_run kind 'read': turning an image into text (a model call), logged like any other.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-20
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE lineage.source_document ADD COLUMN text_uri text;
        ALTER TABLE intake.extraction_run DROP CONSTRAINT extraction_run_kind_check;
        ALTER TABLE intake.extraction_run ADD CONSTRAINT extraction_run_kind_check
            CHECK (kind IN ('classify', 'extract', 'consolidate', 'engine', 'read'));
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM intake.extraction_run WHERE kind = 'read';
        ALTER TABLE intake.extraction_run DROP CONSTRAINT extraction_run_kind_check;
        ALTER TABLE intake.extraction_run ADD CONSTRAINT extraction_run_kind_check
            CHECK (kind IN ('classify', 'extract', 'consolidate', 'engine'));
        ALTER TABLE lineage.source_document DROP COLUMN text_uri;
    """)
