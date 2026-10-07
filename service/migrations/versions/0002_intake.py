"""Intake schema for incoming RFPs, and lineage links to it.

An envelope is one RFP pack. Its files live in lineage.source_document and its
extracted values in lineage.field_value, the same tables the archive uses.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA intake;

        CREATE TABLE intake.envelope (
          envelope_id bigserial PRIMARY KEY,
          title text,
          channel text NOT NULL CHECK (channel IN ('email', 'sharepoint', 'upload')),
          sender text,
          subject text,
          received_at timestamptz NOT NULL DEFAULT now(),
          status text NOT NULL DEFAULT 'received'
            CHECK (status IN ('received', 'reading', 'extracting', 'review', 'confirmed', 'failed')),
          lead_status text CHECK (lead_status IN ('qualified', 'needs_info', 'recommend_no_bid')),
          completeness numeric(4, 3),
          country char(2) REFERENCES ref.country(code),
          location_id int REFERENCES ref.location(location_id),
          billing_currency char(3),
          routed_studio text REFERENCES ref.studio(code),
          submission_deadline timestamptz,
          owner text,
          dataset_version text NOT NULL,
          error text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE intake.extraction_run (
          run_id bigserial PRIMARY KEY,
          envelope_id bigint NOT NULL REFERENCES intake.envelope ON DELETE CASCADE,
          document_id bigint REFERENCES lineage.source_document,
          kind text NOT NULL CHECK (kind IN ('classify', 'extract', 'consolidate', 'engine')),
          model text,
          prompt_version text,
          schema_version text NOT NULL,
          engine_version text,
          dataset_version text NOT NULL,
          status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
          trace_id uuid NOT NULL,
          error text,
          started_at timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz
        );
        CREATE INDEX extraction_run_envelope ON intake.extraction_run (envelope_id);

        CREATE TABLE intake.llm_call (
          call_id bigserial PRIMARY KEY,
          run_id bigint REFERENCES intake.extraction_run ON DELETE CASCADE,
          trace_id uuid NOT NULL,
          attempt int NOT NULL,
          model text NOT NULL,
          prompt_version text NOT NULL,
          request_hash char(64) NOT NULL,
          input_tokens int,
          output_tokens int,
          cache_read_tokens int,
          cache_write_tokens int,
          latency_ms int,
          cost_usd numeric(10, 6),
          stop_reason text,
          response jsonb,
          validation_error text,
          error text,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX llm_call_trace ON intake.llm_call (trace_id);

        CREATE TABLE intake.field_conflict (
          conflict_id bigserial PRIMARY KEY,
          envelope_id bigint NOT NULL REFERENCES intake.envelope ON DELETE CASCADE,
          field text NOT NULL,
          field_value_ids bigint[] NOT NULL,
          summary text NOT NULL,
          status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
          resolved_value_id bigint REFERENCES lineage.field_value,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE intake.engine_flag (
          flag_id bigserial PRIMARY KEY,
          envelope_id bigint NOT NULL REFERENCES intake.envelope ON DELETE CASCADE,
          run_id bigint REFERENCES intake.extraction_run,
          field_value_id bigint REFERENCES lineage.field_value,
          field text,
          rule text NOT NULL,
          severity text NOT NULL CHECK (severity IN ('info', 'warn', 'block')),
          message text NOT NULL,
          evidence jsonb,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE intake.opportunity (
          envelope_id bigint REFERENCES intake.envelope,
          version int,
          record jsonb NOT NULL,
          schema_version text NOT NULL,
          confirmed_by text NOT NULL,
          confirmed_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (envelope_id, version)
        );

        CREATE TRIGGER opportunity_locked
          BEFORE UPDATE OR DELETE ON intake.opportunity
          FOR EACH ROW EXECUTE FUNCTION lineage.forbid_mutation();

        ALTER TABLE lineage.source_document
          ADD COLUMN envelope_id bigint REFERENCES intake.envelope ON DELETE CASCADE;

        ALTER TABLE lineage.field_value
          ADD COLUMN envelope_id bigint REFERENCES intake.envelope ON DELETE CASCADE,
          ADD COLUMN run_id bigint REFERENCES intake.extraction_run,
          ADD CONSTRAINT field_value_one_owner CHECK (num_nonnulls(proposal_id, envelope_id) = 1);
        CREATE INDEX field_value_envelope ON lineage.field_value (envelope_id, field);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE lineage.field_value
          DROP CONSTRAINT field_value_one_owner,
          DROP COLUMN run_id,
          DROP COLUMN envelope_id;
        ALTER TABLE lineage.source_document DROP COLUMN envelope_id;
        DROP SCHEMA intake CASCADE;
        """
    )
