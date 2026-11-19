"""Standard proposal text per workspace: scope per service and stage, exclusions, terms, and the firm's
positions on clauses clients commonly ask to change.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ORG = "org_id uuid NOT NULL DEFAULT tenancy.current_org() REFERENCES tenancy.organization ON DELETE CASCADE"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE ref.clause (
          {ORG},
          kind text NOT NULL CHECK (kind IN ('scope', 'exclusion', 'terms', 'position')),
          key text NOT NULL,          -- scope: service.stage; exclusion: e1..; terms: standard, signoff; position: clause
          service text REFERENCES ref.service(code),
          stage text REFERENCES ref.stage(code),
          seq int NOT NULL DEFAULT 0,
          label text,
          body text NOT NULL CHECK (body <> ''),
          updated_at timestamptz NOT NULL DEFAULT now(),
          updated_by text,
          PRIMARY KEY (org_id, kind, key)
        );
        ALTER TABLE ref.clause ENABLE ROW LEVEL SECURITY;
        ALTER TABLE ref.clause FORCE ROW LEVEL SECURITY;
        CREATE POLICY workspace ON ref.clause
          USING (org_id = tenancy.current_org()) WITH CHECK (org_id = tenancy.current_org());
        GRANT SELECT, INSERT, UPDATE, DELETE ON ref.clause TO bidwright_app, bidwright_studio;

        ALTER TABLE ref.setting_change DROP CONSTRAINT setting_change_setting_check;
        ALTER TABLE ref.setting_change ADD CONSTRAINT setting_change_setting_check
          CHECK (setting IN ('fee_assumption', 'market', 'construction_cost', 'clause'));
        """
    )


def downgrade() -> None:
    op.execute("""
        DELETE FROM ref.setting_change WHERE setting = 'clause';
        ALTER TABLE ref.setting_change DROP CONSTRAINT setting_change_setting_check;
        ALTER TABLE ref.setting_change ADD CONSTRAINT setting_change_setting_check
          CHECK (setting IN ('fee_assumption', 'market', 'construction_cost'));
        DROP TABLE ref.clause;
        """)
