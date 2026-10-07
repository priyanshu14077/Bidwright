"""Rendered PDF per source document.

Every readable file (docx, xlsx, email) is rendered to PDF once, so layout
reading and source highlighting work the same way for all of them. The
original stays untouched at blob_uri.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE lineage.source_document ADD COLUMN render_uri text")


def downgrade() -> None:
    op.execute("ALTER TABLE lineage.source_document DROP COLUMN render_uri")
