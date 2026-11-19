"""Studio edits are logged with the other settings changes.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE ref.setting_change DROP CONSTRAINT setting_change_setting_check;
        ALTER TABLE ref.setting_change ADD CONSTRAINT setting_change_setting_check
          CHECK (setting IN ('fee_assumption', 'market', 'construction_cost', 'clause', 'studio'));
        """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM ref.setting_change WHERE setting = 'studio';
        ALTER TABLE ref.setting_change DROP CONSTRAINT setting_change_setting_check;
        ALTER TABLE ref.setting_change ADD CONSTRAINT setting_change_setting_check
          CHECK (setting IN ('fee_assumption', 'market', 'construction_cost', 'clause'));
        """)
