"""Baseline: tenancy, reference data, archive, lineage, intake and evaluation.

Replaces the four proof-of-concept migrations. No deployed database ran them;
local databases are rebuilt with `make reset`.

Tenancy:
- tenancy.* holds workspaces (organizations), users, memberships, sessions and
  invitations. Only the auth code reads these, and it filters explicitly.
- Every table a workspace owns has org_id, defaulting to tenancy.current_org(),
  which reads the per-transaction setting app.org_id. Natural keys include
  org_id, so two workspaces can both have a proposal 'P01' or a studio 'DXB'.
- Row-level security on those tables: a row is visible and writable only when
  its org_id matches app.org_id. With no setting, nothing is visible and
  inserts fail (org_id is NOT NULL). FORCE applies the policies to the table
  owner too, so the seed cannot write into the wrong workspace either.
- The shared vocabulary (typology, service, stage), countries, FX, escalation
  indices and schema definitions are market facts with no org_id.

Revision ID: 0001
Revises:
Create Date: 2026-10-09
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = ("tenancy", "ref", "archive", "lineage", "intake", "eval")

TENANT_TABLES = (
    "ref.term_synonym", "ref.studio", "ref.location", "ref.benchmark_source", "ref.qualification_rule",
    "ref.field_criticality", "ref.fee_assumption", "ref.typology_cost_ratio",
    "archive.dataset", "archive.client", "archive.proposal", "archive.proposal_service", "archive.proposal_stage",
    "archive.fee_line", "archive.reimbursable", "archive.payment_milestone", "archive.deliverable",
    "archive.tc_deviation", "archive.proposal_section", "archive.pricing_trace",
    "lineage.source_document", "lineage.page", "lineage.field_value", "lineage.field_change",
    "intake.envelope", "intake.extraction_run", "intake.llm_call", "intake.field_conflict", "intake.engine_flag",
    "intake.opportunity",
    "eval.gold_case", "eval.gold_truth", "eval.gold_conflict", "eval.run", "eval.case_result", "eval.field_result",
)

SHARED_TABLES = ("ref.country", "ref.typology", "ref.service", "ref.stage", "ref.fx_rate", "ref.escalation_index",
                 "ref.schema_definition")

ORG = "org_id uuid NOT NULL DEFAULT tenancy.current_org() REFERENCES tenancy.organization ON DELETE CASCADE"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE SCHEMA tenancy;
        CREATE SCHEMA ref;
        CREATE SCHEMA archive;
        CREATE SCHEMA lineage;
        CREATE SCHEMA intake;
        CREATE SCHEMA eval;

        -- ------------------------------------------------------------ tenancy
        CREATE TABLE tenancy.organization (
          org_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{{1,46}}[a-z0-9]$'),
          name text NOT NULL CHECK (length(trim(name)) > 0),
          -- how the extraction prompt describes the practice
          practice_description text NOT NULL DEFAULT 'an architecture and design practice',
          is_demo boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE tenancy.app_user (
          user_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          email text NOT NULL UNIQUE CHECK (email = lower(email) AND email LIKE '%_@_%'),
          name text NOT NULL,
          password_hash text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          last_login_at timestamptz
        );

        CREATE TABLE tenancy.membership (
          org_id uuid REFERENCES tenancy.organization ON DELETE CASCADE,
          user_id uuid REFERENCES tenancy.app_user ON DELETE CASCADE,
          role text NOT NULL CHECK (role IN ('owner', 'admin', 'estimator', 'viewer')),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (org_id, user_id)
        );
        CREATE INDEX membership_by_user ON tenancy.membership (user_id);

        -- Only the SHA-256 of a session token is stored; the token itself lives in the browser cookie.
        CREATE TABLE tenancy.session (
          token_hash char(64) PRIMARY KEY,
          user_id uuid NOT NULL REFERENCES tenancy.app_user ON DELETE CASCADE,
          org_id uuid REFERENCES tenancy.organization ON DELETE SET NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL
        );
        CREATE INDEX session_by_user ON tenancy.session (user_id);

        CREATE TABLE tenancy.invitation (
          token_hash char(64) PRIMARY KEY,
          org_id uuid NOT NULL REFERENCES tenancy.organization ON DELETE CASCADE,
          email text NOT NULL CHECK (email = lower(email)),
          role text NOT NULL CHECK (role IN ('admin', 'estimator', 'viewer')),
          invited_by uuid REFERENCES tenancy.app_user ON DELETE SET NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          accepted_at timestamptz
        );

        CREATE FUNCTION tenancy.current_org() RETURNS uuid LANGUAGE sql STABLE AS $$
          SELECT NULLIF(current_setting('app.org_id', true), '')::uuid
        $$;

        -- ------------------------------------------------------------ ref: shared
        CREATE TABLE ref.country (
          code char(2) PRIMARY KEY,
          name text NOT NULL,
          region text NOT NULL,
          national_currency char(3) NOT NULL,
          area_unit text NOT NULL CHECK (area_unit IN ('m2', 'sqft')),
          aliases text[] NOT NULL DEFAULT '{{}}'
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

        CREATE TABLE ref.schema_definition (
          version text PRIMARY KEY,
          definition jsonb NOT NULL,
          loaded_at timestamptz NOT NULL DEFAULT now()
        );

        -- ------------------------------------------------------------ ref: per workspace
        CREATE TABLE ref.term_synonym (
          {ORG},
          synonym text NOT NULL CHECK (synonym = lower(synonym)),
          target_table text NOT NULL CHECK (target_table IN ('typology', 'service', 'stage')),
          target_code text NOT NULL,
          scope text NOT NULL DEFAULT 'global',
          language text NOT NULL DEFAULT 'en',
          PRIMARY KEY (org_id, synonym, target_table, scope)
        );

        CREATE TABLE ref.studio (
          {ORG},
          code text NOT NULL,
          name text NOT NULL,
          city text NOT NULL,
          country char(2) NOT NULL REFERENCES ref.country(code),
          PRIMARY KEY (org_id, code)
        );

        CREATE TABLE ref.location (
          location_id serial PRIMARY KEY,
          {ORG},
          country char(2) NOT NULL REFERENCES ref.country(code),
          city text NOT NULL,
          tier text NOT NULL CHECK (tier IN ('prime', 'second')),
          aliases text[] NOT NULL DEFAULT '{{}}',
          UNIQUE (org_id, country, city),
          UNIQUE (org_id, location_id)
        );

        CREATE TABLE ref.benchmark_source (
          source_id serial PRIMARY KEY,
          {ORG},
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
          {ORG},
          name text NOT NULL,
          expression jsonb NOT NULL,
          effect text NOT NULL CHECK (effect IN ('flag', 'needs_info', 'recommend_no_bid')),
          message text NOT NULL,
          active boolean NOT NULL DEFAULT true,
          owner text,
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, name)
        );

        CREATE TABLE ref.field_criticality (
          {ORG},
          field text NOT NULL,
          pricing_critical boolean NOT NULL,
          weight numeric(4, 2) NOT NULL,
          PRIMARY KEY (org_id, field)
        );

        CREATE TABLE ref.fee_assumption (
          {ORG},
          key text NOT NULL,
          value_text text NOT NULL,
          value numeric,  -- set when value_text is a plain number
          unit text,
          rationale text,
          dataset_version text NOT NULL,
          PRIMARY KEY (org_id, key)
        );

        CREATE TABLE ref.typology_cost_ratio (
          {ORG},
          cost_type text NOT NULL,
          ratio numeric NOT NULL,
          dataset_version text NOT NULL,
          PRIMARY KEY (org_id, cost_type)
        );

        -- ------------------------------------------------------------ archive
        CREATE TABLE archive.dataset (
          {ORG},
          dataset_version text NOT NULL,
          name text NOT NULL,
          data_origin text NOT NULL CHECK (data_origin IN ('archive', 'synthetic')),
          generated date,
          record_count int,
          warning text,
          loaded_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (org_id, dataset_version)
        );

        CREATE TABLE archive.client (
          {ORG},
          client_id text NOT NULL,
          dataset_version text NOT NULL,
          name text NOT NULL,
          client_type text,
          country char(2) REFERENCES ref.country(code),
          first_engaged date,
          PRIMARY KEY (org_id, client_id)
        );
        CREATE INDEX client_name_trgm ON archive.client USING gin (name gin_trgm_ops);

        CREATE TABLE archive.proposal (
          {ORG},
          proposal_id text NOT NULL,
          dataset_version text NOT NULL,
          reference text NOT NULL,
          title text NOT NULL,
          data_origin text NOT NULL CHECK (data_origin IN ('archive', 'synthetic', 'rfp_new')),
          studio_code text,
          client_id text,
          -- request side
          client_status text CHECK (client_status IN ('new', 'repeat')),
          location_id int,
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
          cost_city text,
          document_variations jsonb,
          schema_version text NOT NULL DEFAULT '0.1',
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (org_id, proposal_id),
          UNIQUE (org_id, reference),
          FOREIGN KEY (org_id, studio_code) REFERENCES ref.studio (org_id, code),
          FOREIGN KEY (org_id, client_id) REFERENCES archive.client (org_id, client_id),
          FOREIGN KEY (org_id, location_id) REFERENCES ref.location (org_id, location_id)
        );
        CREATE INDEX proposal_dataset ON archive.proposal (org_id, dataset_version);

        CREATE TABLE archive.proposal_service (
          {ORG},
          proposal_id text NOT NULL,
          service text REFERENCES ref.service,
          PRIMARY KEY (org_id, proposal_id, service),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );

        CREATE TABLE archive.proposal_stage (
          {ORG},
          proposal_id text NOT NULL,
          stage text REFERENCES ref.stage,
          PRIMARY KEY (org_id, proposal_id, stage),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );

        CREATE TABLE archive.fee_line (
          {ORG},
          proposal_id text NOT NULL,
          line_no int,
          service text NOT NULL REFERENCES ref.service,
          stage text NOT NULL REFERENCES ref.stage,
          basis text CHECK (basis IN ('pct_of_construction_cost', 'pct_of_fitout_cost',
                                      'pct_of_landscape_cost', 'per_hectare', 'lump_sum')),
          basis_qty numeric,
          basis_unit text,
          amount_local numeric NOT NULL,
          amount_usd numeric,
          PRIMARY KEY (org_id, proposal_id, line_no),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );

        CREATE TABLE archive.reimbursable (
          {ORG},
          proposal_id text NOT NULL,
          type text CHECK (type IN ('renders', 'site_trip', 'other')),
          qty int,
          unit_rate_local numeric,
          amount_local numeric,
          note text,
          PRIMARY KEY (org_id, proposal_id, type),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );

        CREATE TABLE archive.payment_milestone (
          {ORG},
          proposal_id text NOT NULL,
          seq int,
          milestone text NOT NULL,
          pct numeric(5, 2) NOT NULL,
          amount_local numeric,
          PRIMARY KEY (org_id, proposal_id, seq),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );

        CREATE TABLE archive.deliverable (
          {ORG},
          proposal_id text NOT NULL,
          item text,
          qty int,
          PRIMARY KEY (org_id, proposal_id, item),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );

        CREATE TABLE archive.tc_deviation (
          {ORG},
          proposal_id text NOT NULL,
          clause text,
          client_request text,
          firm_position text,
          PRIMARY KEY (org_id, proposal_id, clause),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );

        CREATE TABLE archive.proposal_section (
          section_id bigserial PRIMARY KEY,
          {ORG},
          proposal_id text NOT NULL,
          seq int NOT NULL,
          section_code text NOT NULL,
          heading text NOT NULL,
          service text,
          stage text,
          body text NOT NULL,
          UNIQUE (org_id, proposal_id, seq),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );

        -- Synthetic data only: how each record was generated.
        CREATE TABLE archive.pricing_trace (
          trace_id bigserial PRIMARY KEY,
          {ORG},
          proposal_id text NOT NULL,
          factor text NOT NULL,
          value text,
          source text,
          note text,
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );

        -- ------------------------------------------------------------ intake (envelope first: lineage refers to it)
        CREATE TABLE intake.envelope (
          envelope_id bigserial PRIMARY KEY,
          {ORG},
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
          location_id int,
          billing_currency char(3),
          routed_studio text,
          submission_deadline timestamptz,
          owner text,
          dataset_version text NOT NULL,
          error text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, envelope_id),
          FOREIGN KEY (org_id, location_id) REFERENCES ref.location (org_id, location_id),
          FOREIGN KEY (org_id, routed_studio) REFERENCES ref.studio (org_id, code)
        );
        CREATE INDEX envelope_received ON intake.envelope (org_id, received_at DESC);

        -- ------------------------------------------------------------ lineage
        CREATE TABLE lineage.source_document (
          document_id bigserial PRIMARY KEY,
          {ORG},
          proposal_id text,
          envelope_id bigint,
          parent_document_id bigint REFERENCES lineage.source_document,
          channel text NOT NULL CHECK (channel IN ('email', 'sharepoint', 'upload', 'archive')),
          file_name text NOT NULL,
          mime_type text,
          size_bytes bigint,
          sha256 char(64) NOT NULL,
          blob_uri text NOT NULL,
          render_uri text,
          sender text,
          received_at timestamptz NOT NULL DEFAULT now(),
          doc_class text,
          language text,
          page_count int,
          supported boolean NOT NULL DEFAULT true,
          UNIQUE (org_id, sha256),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE,
          FOREIGN KEY (org_id, envelope_id) REFERENCES intake.envelope (org_id, envelope_id) ON DELETE CASCADE
        );

        CREATE TABLE lineage.page (
          {ORG},
          document_id bigint REFERENCES lineage.source_document ON DELETE CASCADE,
          page_no int,
          width numeric NOT NULL,
          height numeric NOT NULL,
          lines jsonb NOT NULL,
          PRIMARY KEY (document_id, page_no)
        );

        CREATE TABLE intake.extraction_run (
          run_id bigserial PRIMARY KEY,
          {ORG},
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

        CREATE TABLE lineage.field_value (
          field_value_id bigserial PRIMARY KEY,
          {ORG},
          proposal_id text,
          envelope_id bigint REFERENCES intake.envelope ON DELETE CASCADE,
          run_id bigint REFERENCES intake.extraction_run,
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
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT field_value_one_owner CHECK (num_nonnulls(proposal_id, envelope_id) = 1),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );
        CREATE INDEX field_value_proposal ON lineage.field_value (org_id, proposal_id, field);
        CREATE INDEX field_value_envelope ON lineage.field_value (envelope_id, field);

        CREATE TABLE lineage.field_change (
          change_id bigserial PRIMARY KEY,
          {ORG},
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

        -- ------------------------------------------------------------ intake, continued
        CREATE TABLE intake.llm_call (
          call_id bigserial PRIMARY KEY,
          {ORG},
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
          {ORG},
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
          {ORG},
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
          {ORG},
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

        -- ------------------------------------------------------------ eval
        CREATE TABLE eval.gold_case (
          {ORG},
          dataset_version text NOT NULL,
          case_id text NOT NULL,
          proposal_id text NOT NULL,
          style text,
          issued_at timestamptz NOT NULL,
          traps text[] NOT NULL DEFAULT '{{}}',
          special_requests text[] NOT NULL DEFAULT '{{}}',
          envelope_id bigint REFERENCES intake.envelope ON DELETE SET NULL,
          PRIMARY KEY (org_id, dataset_version, case_id),
          FOREIGN KEY (org_id, proposal_id) REFERENCES archive.proposal ON DELETE CASCADE
        );

        CREATE TABLE eval.gold_truth (
          {ORG},
          dataset_version text NOT NULL,
          case_id text NOT NULL,
          field text NOT NULL,
          value jsonb,
          PRIMARY KEY (org_id, dataset_version, case_id, field),
          FOREIGN KEY (org_id, dataset_version, case_id) REFERENCES eval.gold_case ON DELETE CASCADE
        );

        CREATE TABLE eval.gold_conflict (
          {ORG},
          dataset_version text NOT NULL,
          case_id text NOT NULL,
          field text NOT NULL,
          values jsonb NOT NULL,
          documents text[] NOT NULL,
          PRIMARY KEY (org_id, dataset_version, case_id, field),
          FOREIGN KEY (org_id, dataset_version, case_id) REFERENCES eval.gold_case ON DELETE CASCADE
        );

        -- Runs keep case ids as plain text so history survives a reload of the gold set.
        CREATE TABLE eval.run (
          run_id bigserial PRIMARY KEY,
          {ORG},
          run_at timestamptz NOT NULL DEFAULT now(),
          dataset_version text NOT NULL,
          model text,
          prompt_version text,
          engine_version text,
          summary jsonb NOT NULL
        );

        CREATE TABLE eval.case_result (
          {ORG},
          run_id bigint REFERENCES eval.run ON DELETE CASCADE,
          case_id text,
          envelope_id bigint REFERENCES intake.envelope ON DELETE SET NULL,
          detail jsonb NOT NULL,
          PRIMARY KEY (run_id, case_id)
        );

        CREATE TABLE eval.field_result (
          {ORG},
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

    for table in TENANT_TABLES:
        op.execute(
            f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY workspace ON {table}
              USING (org_id = tenancy.current_org()) WITH CHECK (org_id = tenancy.current_org());
            """
        )

    grants = ";\n".join(
        f"GRANT USAGE ON SCHEMA {s} TO bidwright_app, bidwright_studio; "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {s} TO bidwright_app, bidwright_studio; "
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {s} TO bidwright_app, bidwright_studio"
        for s in SCHEMAS
    ) + (
        # the shared vocabulary and market facts change only through the seed, never from a workspace
        f";\nREVOKE INSERT, UPDATE, DELETE ON {', '.join(SHARED_TABLES)} FROM bidwright_app"
        ";\nGRANT SELECT ON public.alembic_version TO bidwright_app"
    )
    op.execute(f"""DO $$ BEGIN
                     IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidwright_app')
                        AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidwright_studio') THEN {grants};
                     ELSE RAISE EXCEPTION 'run infra/postgres/init/02-roles.sh first (make roles)';
                     END IF;
                   END $$""")


def downgrade() -> None:
    op.execute("DROP SCHEMA eval, intake, lineage, archive, ref, tenancy CASCADE")
