"""Archive baseline: ref, archive and lineage schemas.

Taken from data/archive/v0-synthetic/sog_archive_schema.sql, with these changes:
- ref.country added; location/studio reference ISO country codes.
- ref.term_synonym keyed by (synonym, target_table, scope) so one word can
  map to different codes in different countries.
- dataset_version on archive rows, so synthetic and real archives can coexist.
- lineage.field_value has a surrogate id, JSONB values, quote and kind; several
  candidates per field can coexist (conflicts, re-runs).
- lineage.field_change references the values it links and is append-only by
  trigger (REVOKE does not bind the table owner).
- proposal_section.embedding dropped until an embedding provider is chosen.
- Reference rows are not inserted here; the loader seeds them.

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA ref;
        CREATE SCHEMA archive;
        CREATE SCHEMA lineage;

        -- ------------------------------------------------------------ ref
        CREATE TABLE ref.country (
          code char(2) PRIMARY KEY,
          name text NOT NULL,
          region text NOT NULL,
          national_currency char(3) NOT NULL,
          area_unit text NOT NULL CHECK (area_unit IN ('m2', 'sqft')),
          aliases text[] NOT NULL DEFAULT '{}'
        );

        CREATE TABLE ref.typology (code text PRIMARY KEY, label text NOT NULL);

        CREATE TABLE ref.service (
          code text PRIMARY KEY,
          label text NOT NULL,
          default_area_basis text NOT NULL
        );

        CREATE TABLE ref.stage (
          code text PRIMARY KEY,
          label text NOT NULL,
          seq int NOT NULL UNIQUE
        );

        CREATE TABLE ref.term_synonym (
          synonym text NOT NULL CHECK (synonym = lower(synonym)),
          target_table text NOT NULL CHECK (target_table IN ('typology', 'service', 'stage')),
          target_code text NOT NULL,
          scope text NOT NULL DEFAULT 'global',
          language text NOT NULL DEFAULT 'en',
          PRIMARY KEY (synonym, target_table, scope)
        );

        CREATE TABLE ref.studio (
          code text PRIMARY KEY,
          name text NOT NULL,
          city text NOT NULL,
          country char(2) NOT NULL REFERENCES ref.country(code)
        );

        CREATE TABLE ref.location (
          location_id serial PRIMARY KEY,
          country char(2) NOT NULL REFERENCES ref.country(code),
          city text NOT NULL,
          tier text NOT NULL CHECK (tier IN ('prime', 'second')),
          aliases text[] NOT NULL DEFAULT '{}',
          UNIQUE (country, city)
        );

        CREATE TABLE ref.fx_rate (
          currency char(3) NOT NULL,
          per_usd numeric(14, 6) NOT NULL CHECK (per_usd > 0),
          as_of date NOT NULL,
          note text,
          PRIMARY KEY (currency, as_of)
        );

        CREATE TABLE ref.escalation_index (
          market text NOT NULL,
          year int NOT NULL,
          annual_rate numeric(6, 4) NOT NULL,
          source_key text,
          PRIMARY KEY (market, year)
        );

        CREATE TABLE ref.benchmark_source (
          source_id serial PRIMARY KEY,
          source_key text,
          market text,
          metric text,
          value text,
          unit text,
          year int,
          source_name text,
          url text,
          note text
        );

        CREATE TABLE ref.qualification_rule (
          rule_id serial PRIMARY KEY,
          name text NOT NULL UNIQUE,
          expression jsonb NOT NULL,
          effect text NOT NULL CHECK (effect IN ('flag', 'needs_info', 'recommend_no_bid')),
          message text NOT NULL,
          active boolean NOT NULL DEFAULT true,
          owner text,
          updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE ref.field_criticality (
          field text PRIMARY KEY,
          pricing_critical boolean NOT NULL,
          weight numeric(4, 2) NOT NULL
        );

        -- ------------------------------------------------------------ archive
        CREATE TABLE archive.client (
          client_id text PRIMARY KEY,
          dataset_version text NOT NULL,
          name text NOT NULL,
          client_type text,
          country char(2) REFERENCES ref.country(code),
          first_engaged date
        );
        CREATE INDEX client_name_trgm ON archive.client USING gin (name gin_trgm_ops);

        CREATE TABLE archive.proposal (
          proposal_id text PRIMARY KEY,
          dataset_version text NOT NULL,
          reference text UNIQUE NOT NULL,
          title text NOT NULL,
          data_origin text NOT NULL CHECK (data_origin IN ('archive', 'synthetic', 'rfp_new')),
          studio_code text REFERENCES ref.studio(code),
          client_id text REFERENCES archive.client(client_id),
          -- request side
          client_status text CHECK (client_status IN ('new', 'repeat')),
          location_id int REFERENCES ref.location(location_id),
          typology text REFERENCES ref.typology(code),
          sub_typology text,
          gfa_m2 numeric,
          site_area_m2 numeric,
          landscape_area_m2 numeric,
          fitout_area_m2 numeric,
          units int,
          keys int,
          complexity text CHECK (complexity IN ('low', 'medium', 'high')),
          complexity_notes text,
          -- response side
          submission_date date,
          currency char(3),
          fx_per_usd numeric(14, 6),
          construction_cost_usd_m2 numeric,
          fee_total_local numeric,
          fee_total_usd numeric,
          status text CHECK (status IN ('draft', 'submitted', 'won', 'lost', 'withdrawn')),
          outcome_date date,
          loss_reason text,
          project_challenges text,
          schema_version text NOT NULL DEFAULT '0.1',
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX proposal_dataset ON archive.proposal (dataset_version);

        CREATE TABLE archive.proposal_service (
          proposal_id text REFERENCES archive.proposal ON DELETE CASCADE,
          service text REFERENCES ref.service,
          PRIMARY KEY (proposal_id, service)
        );

        CREATE TABLE archive.proposal_stage (
          proposal_id text REFERENCES archive.proposal ON DELETE CASCADE,
          stage text REFERENCES ref.stage,
          PRIMARY KEY (proposal_id, stage)
        );

        CREATE TABLE archive.fee_line (
          proposal_id text REFERENCES archive.proposal ON DELETE CASCADE,
          line_no int,
          service text NOT NULL REFERENCES ref.service,
          stage text NOT NULL REFERENCES ref.stage,
          basis text CHECK (basis IN ('pct_of_construction_cost', 'pct_of_fitout_cost',
                                      'pct_of_landscape_cost', 'per_hectare', 'lump_sum')),
          basis_qty numeric,
          basis_unit text,
          amount_local numeric NOT NULL,
          amount_usd numeric,
          PRIMARY KEY (proposal_id, line_no)
        );

        CREATE TABLE archive.reimbursable (
          proposal_id text REFERENCES archive.proposal ON DELETE CASCADE,
          type text CHECK (type IN ('renders', 'site_trip', 'other')),
          qty int,
          unit_rate_local numeric,
          amount_local numeric,
          note text,
          PRIMARY KEY (proposal_id, type)
        );

        CREATE TABLE archive.payment_milestone (
          proposal_id text REFERENCES archive.proposal ON DELETE CASCADE,
          seq int,
          milestone text NOT NULL,
          pct numeric(5, 2) NOT NULL,
          amount_local numeric,
          PRIMARY KEY (proposal_id, seq)
        );

        CREATE TABLE archive.deliverable (
          proposal_id text REFERENCES archive.proposal ON DELETE CASCADE,
          item text,
          qty int,
          PRIMARY KEY (proposal_id, item)
        );

        CREATE TABLE archive.tc_deviation (
          proposal_id text REFERENCES archive.proposal ON DELETE CASCADE,
          clause text,
          client_request text,
          sog_position text,
          PRIMARY KEY (proposal_id, clause)
        );

        CREATE TABLE archive.proposal_section (
          section_id bigserial PRIMARY KEY,
          proposal_id text NOT NULL REFERENCES archive.proposal ON DELETE CASCADE,
          seq int NOT NULL,
          section_code text NOT NULL,
          heading text NOT NULL,
          service text,
          stage text,
          body text NOT NULL,
          UNIQUE (proposal_id, seq)
        );

        -- Synthetic data only: how each record was generated.
        CREATE TABLE archive.pricing_trace (
          trace_id bigserial PRIMARY KEY,
          proposal_id text NOT NULL REFERENCES archive.proposal ON DELETE CASCADE,
          factor text NOT NULL,
          value text,
          source text,
          note text
        );

        -- ------------------------------------------------------------ lineage
        CREATE TABLE lineage.source_document (
          document_id bigserial PRIMARY KEY,
          proposal_id text REFERENCES archive.proposal ON DELETE CASCADE,
          parent_document_id bigint REFERENCES lineage.source_document,
          channel text NOT NULL CHECK (channel IN ('email', 'sharepoint', 'upload', 'archive')),
          file_name text NOT NULL,
          mime_type text,
          size_bytes bigint,
          sha256 char(64) NOT NULL UNIQUE,
          blob_uri text NOT NULL,
          sender text,
          received_at timestamptz NOT NULL DEFAULT now(),
          doc_class text,
          language text,
          page_count int,
          supported boolean NOT NULL DEFAULT true
        );

        CREATE TABLE lineage.page (
          document_id bigint REFERENCES lineage.source_document ON DELETE CASCADE,
          page_no int,
          width numeric NOT NULL,
          height numeric NOT NULL,
          lines jsonb NOT NULL,
          PRIMARY KEY (document_id, page_no)
        );

        CREATE TABLE lineage.field_value (
          field_value_id bigserial PRIMARY KEY,
          proposal_id text REFERENCES archive.proposal ON DELETE CASCADE,
          field text NOT NULL,
          value jsonb,
          normalized jsonb,
          document_id bigint REFERENCES lineage.source_document,
          page int,
          quote text,
          bbox jsonb,
          confidence numeric(4, 3),
          origin text NOT NULL CHECK (origin IN ('ai', 'engine', 'human', 'generator')),
          kind text NOT NULL DEFAULT 'fact' CHECK (kind IN ('fact', 'suggestion')),
          status text NOT NULL DEFAULT 'proposed'
            CHECK (status IN ('proposed', 'confirmed', 'rejected', 'superseded')),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX field_value_proposal ON lineage.field_value (proposal_id, field);

        CREATE TABLE lineage.field_change (
          change_id bigserial PRIMARY KEY,
          field text NOT NULL,
          old_field_value_id bigint REFERENCES lineage.field_value,
          new_field_value_id bigint REFERENCES lineage.field_value,
          old_value jsonb,
          new_value jsonb,
          action text NOT NULL CHECK (action IN ('edit', 'accept', 'reject', 'resolve_conflict', 'confirm')),
          reason text NOT NULL CHECK (length(trim(reason)) > 0),
          changed_by text NOT NULL,
          changed_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE FUNCTION lineage.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME;
        END $$;

        CREATE TRIGGER field_change_append_only
          BEFORE UPDATE OR DELETE ON lineage.field_change
          FOR EACH ROW EXECUTE FUNCTION lineage.forbid_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA lineage CASCADE; DROP SCHEMA archive CASCADE; DROP SCHEMA ref CASCADE;")
