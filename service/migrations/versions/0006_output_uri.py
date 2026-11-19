"""Exports keep where their file is stored, not only its hash, so the blob store can change.
The id column is renamed to match the table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-18
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE proposal.output RENAME COLUMN export_id TO output_id;
        ALTER TABLE proposal.output ADD COLUMN blob_uri text NOT NULL;
        ALTER TABLE proposal.output ADD COLUMN size_bytes int NOT NULL;
        CREATE INDEX output_by_version ON proposal.output (version_id, created_at);
        """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX proposal.output_by_version;
        ALTER TABLE proposal.output DROP COLUMN size_bytes, DROP COLUMN blob_uri;
        ALTER TABLE proposal.output RENAME COLUMN output_id TO export_id;
        """)
