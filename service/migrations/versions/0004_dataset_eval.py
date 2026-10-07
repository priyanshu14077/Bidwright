"""Everything in the synthetic dataset that was still only on disk, plus the gold set.

- archive.dataset: one row per loaded dataset version (synthetic now, real later).
- ref.fee_assumption, ref.typology_cost_ratio: the fee-model parameters behind the
  synthetic fees. Phase 2 pricing calibrates these against the real archive.
- ref.schema_definition: the proposal request schema, versioned.
- archive.proposal.cost_city, document_variations: how each document was written.
- eval: gold cases (RFP pack -> expected values), and every evaluation run.
- Grants for sog_prisma (DML only, no DDL) when that role exists.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SCHEMAS = ("ref", "archive", "lineage", "intake", "eval")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE archive.dataset (
          dataset_version text PRIMARY KEY,
          name text NOT NULL,
          data_origin text NOT NULL CHECK (data_origin IN ('archive', 'synthetic')),
          generated date,
          record_count int,
          warning text,
          loaded_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE ref.fee_assumption (
          key text PRIMARY KEY,
          value_text text NOT NULL,
          value numeric,  -- set when value_text is a plain number
          unit text,
          rationale text,
          dataset_version text NOT NULL
        );

        CREATE TABLE ref.typology_cost_ratio (
          cost_type text PRIMARY KEY,
          ratio numeric NOT NULL,
          dataset_version text NOT NULL
        );

        CREATE TABLE ref.schema_definition (
          version text PRIMARY KEY,
          definition jsonb NOT NULL,
          loaded_at timestamptz NOT NULL DEFAULT now()
        );

        ALTER TABLE archive.proposal
          ADD COLUMN cost_city text,
          ADD COLUMN document_variations jsonb;

        CREATE SCHEMA eval;

        CREATE TABLE eval.gold_case (
          dataset_version text NOT NULL,
          case_id text NOT NULL,
          proposal_id text NOT NULL REFERENCES archive.proposal ON DELETE CASCADE,
          style text,
          issued_at timestamptz NOT NULL,
          traps text[] NOT NULL DEFAULT '{}',
          special_requests text[] NOT NULL DEFAULT '{}',
          envelope_id bigint REFERENCES intake.envelope ON DELETE SET NULL,
          PRIMARY KEY (dataset_version, case_id)
        );

        CREATE TABLE eval.gold_truth (
          dataset_version text NOT NULL,
          case_id text NOT NULL,
          field text NOT NULL,
          value jsonb,
          PRIMARY KEY (dataset_version, case_id, field),
          FOREIGN KEY (dataset_version, case_id) REFERENCES eval.gold_case ON DELETE CASCADE
        );

        CREATE TABLE eval.gold_conflict (
          dataset_version text NOT NULL,
          case_id text NOT NULL,
          field text NOT NULL,
          values jsonb NOT NULL,
          documents text[] NOT NULL,
          PRIMARY KEY (dataset_version, case_id, field),
          FOREIGN KEY (dataset_version, case_id) REFERENCES eval.gold_case ON DELETE CASCADE
        );

        -- Runs keep case ids as plain text so history survives a reload of the gold set.
        CREATE TABLE eval.run (
          run_id bigserial PRIMARY KEY,
          run_at timestamptz NOT NULL DEFAULT now(),
          dataset_version text NOT NULL,
          model text,
          prompt_version text,
          engine_version text,
          summary jsonb NOT NULL
        );

        CREATE TABLE eval.case_result (
          run_id bigint REFERENCES eval.run ON DELETE CASCADE,
          case_id text,
          envelope_id bigint REFERENCES intake.envelope ON DELETE SET NULL,
          detail jsonb NOT NULL,
          PRIMARY KEY (run_id, case_id)
        );

        CREATE TABLE eval.field_result (
          run_id bigint REFERENCES eval.run ON DELETE CASCADE,
          case_id text,
          field text,
          truth jsonb,
          got jsonb,
          ok boolean NOT NULL,
          PRIMARY KEY (run_id, case_id, field)
        );
        """
    )
    grants = ";\n".join(
        f"GRANT USAGE ON SCHEMA {s} TO sog_prisma; "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {s} TO sog_prisma; "
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {s} TO sog_prisma; "
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {s} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sog_prisma; "
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {s} GRANT USAGE, SELECT ON SEQUENCES TO sog_prisma"
        for s in SCHEMAS
    )
    op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sog_prisma') THEN {grants}; END IF; END $$")


def downgrade() -> None:
    op.execute(
        """
        DROP SCHEMA eval CASCADE;
        ALTER TABLE archive.proposal DROP COLUMN document_variations, DROP COLUMN cost_city;
        DROP TABLE ref.schema_definition, ref.typology_cost_ratio, ref.fee_assumption, archive.dataset;
        """
    )
