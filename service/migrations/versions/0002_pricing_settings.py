"""Pricing settings per workspace: markets, the construction cost book, and a change log.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ORG = "org_id uuid NOT NULL DEFAULT tenancy.current_org() REFERENCES tenancy.organization ON DELETE CASCADE"
TABLES = ("ref.market", "ref.construction_cost", "ref.setting_change")
COST_TYPES = ("average", "residential_highrise", "residential_villa", "office", "mixed_use",
              "hospitality_5star", "hospitality_3star")


def upgrade() -> None:
    cost_types = ", ".join(f"'{t}'" for t in COST_TYPES)
    op.execute(
        f"""
        -- Per country: the cost band (landscape and render rates), the architecture fee % factor,
        -- and how fast construction cost escalates.
        CREATE TABLE ref.market (
          {ORG},
          country char(2) NOT NULL REFERENCES ref.country(code),
          band text NOT NULL CHECK (band IN ('high', 'low')),
          fee_factor numeric NOT NULL CHECK (fee_factor > 0),
          fee_factor_source text,
          escalation_pct numeric NOT NULL CHECK (escalation_pct > -50 AND escalation_pct < 100),
          escalation_source text,
          updated_at timestamptz NOT NULL DEFAULT now(),
          updated_by text,
          PRIMARY KEY (org_id, country)
        );

        -- Building cost per m² of GFA in USD at price_year prices. city '' is the country-wide figure.
        CREATE TABLE ref.construction_cost (
          {ORG},
          country char(2) NOT NULL REFERENCES ref.country(code),
          city text NOT NULL DEFAULT '',
          cost_type text NOT NULL CHECK (cost_type IN ({cost_types})),
          usd_m2 numeric NOT NULL CHECK (usd_m2 > 0),
          price_year int NOT NULL CHECK (price_year BETWEEN 2000 AND 2100),
          basis text NOT NULL CHECK (basis IN ('benchmark', 'derived', 'proxy', 'synthetic', 'estimator')),
          source text,
          note text,
          updated_at timestamptz NOT NULL DEFAULT now(),
          updated_by text,
          PRIMARY KEY (org_id, country, city, cost_type)
        );

        CREATE TABLE ref.setting_change (
          change_id bigserial PRIMARY KEY,
          {ORG},
          setting text NOT NULL CHECK (setting IN ('fee_assumption', 'market', 'construction_cost')),
          key text NOT NULL,
          old jsonb,
          new jsonb,
          reason text,
          changed_by text NOT NULL,
          changed_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX setting_change_by_time ON ref.setting_change (org_id, changed_at DESC);
        """
    )
    for table in TABLES:
        op.execute(
            f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY workspace ON {table}
              USING (org_id = tenancy.current_org()) WITH CHECK (org_id = tenancy.current_org());
            GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO bidwright_app, bidwright_studio;
            """
        )
    op.execute("GRANT USAGE, SELECT ON SEQUENCE ref.setting_change_change_id_seq TO bidwright_app, bidwright_studio")
    # The change log is append-only for the app.
    op.execute("REVOKE UPDATE, DELETE ON ref.setting_change FROM bidwright_app")


def downgrade() -> None:
    op.execute("DROP TABLE ref.setting_change, ref.construction_cost, ref.market")
