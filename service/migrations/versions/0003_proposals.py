"""Proposals: a priced, editable, versioned fee proposal for a confirmed request.

A version is a draft until an admin approves it. From then on the database
refuses any change to it or its lines and sections, except marking it
superseded when a later version is approved.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ORG = "org_id uuid NOT NULL DEFAULT tenancy.current_org() REFERENCES tenancy.organization ON DELETE CASCADE"
TABLES = ("proposal.bid", "proposal.version", "proposal.line", "proposal.section", "proposal.event",
          "proposal.output")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE SCHEMA proposal;

        CREATE TABLE proposal.bid (
          proposal_id bigserial PRIMARY KEY,
          {ORG},
          reference text NOT NULL,
          envelope_id bigint NOT NULL REFERENCES intake.envelope ON DELETE CASCADE,
          title text NOT NULL,
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (org_id, reference)
        );

        CREATE TABLE proposal.version (
          version_id bigserial PRIMARY KEY,
          {ORG},
          proposal_id bigint NOT NULL REFERENCES proposal.bid ON DELETE CASCADE,
          version_no int NOT NULL CHECK (version_no > 0),
          status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'superseded')),
          based_on bigint REFERENCES proposal.version,
          record_version int NOT NULL,          -- the intake.opportunity version it was priced from
          pricing_choices jsonb NOT NULL DEFAULT '{{}}',
          estimate jsonb NOT NULL,              -- the fee engine's output, as priced
          engine_version text NOT NULL,
          currency char(3) NOT NULL,
          fx_per_usd numeric NOT NULL,
          engine_total_local numeric NOT NULL,
          fee_total_local numeric NOT NULL,     -- after the estimator's edits
          revision int NOT NULL DEFAULT 1,      -- optimistic concurrency: bumped by every edit
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          approved_by text,
          approved_at timestamptz,
          superseded_at timestamptz,
          UNIQUE (proposal_id, version_no),
          CHECK ((status = 'draft') = (approved_at IS NULL))
        );
        CREATE UNIQUE INDEX one_draft_per_proposal ON proposal.version (proposal_id) WHERE status = 'draft';

        CREATE TABLE proposal.line (
          version_id bigint NOT NULL REFERENCES proposal.version ON DELETE CASCADE,
          line_no int NOT NULL,
          {ORG},
          service text NOT NULL REFERENCES ref.service(code),
          stage text NOT NULL REFERENCES ref.stage(code),
          basis text,
          basis_qty numeric,
          basis_unit text,
          engine_amount_local numeric,          -- null for a line the estimator added
          amount_local numeric NOT NULL CHECK (amount_local >= 0),
          factors jsonb NOT NULL DEFAULT '[]',
          note text,
          PRIMARY KEY (version_id, line_no)
        );

        CREATE TABLE proposal.section (
          version_id bigint NOT NULL REFERENCES proposal.version ON DELETE CASCADE,
          code text NOT NULL,
          {ORG},
          seq int NOT NULL,
          title text NOT NULL,
          kind text NOT NULL CHECK (kind IN ('cover', 'prose', 'table')),
          body text,
          data jsonb,
          origin text NOT NULL CHECK (origin IN ('record', 'engine', 'library', 'claude', 'estimator', 'pending')),
          citations jsonb NOT NULL DEFAULT '[]',
          updated_by text,
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (version_id, code)
        );

        CREATE TABLE proposal.event (
          event_id bigserial PRIMARY KEY,
          {ORG},
          proposal_id bigint NOT NULL REFERENCES proposal.bid ON DELETE CASCADE,
          version_id bigint REFERENCES proposal.version ON DELETE CASCADE,
          action text NOT NULL,
          target text,
          old jsonb,
          new jsonb,
          reason text,
          actor text NOT NULL,
          at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX event_by_proposal ON proposal.event (proposal_id, at);

        CREATE TABLE proposal.output (
          export_id bigserial PRIMARY KEY,
          {ORG},
          version_id bigint NOT NULL REFERENCES proposal.version ON DELETE CASCADE,
          format text NOT NULL CHECK (format IN ('docx', 'pdf')),
          blob_sha char(64) NOT NULL,
          file_name text NOT NULL,
          template text NOT NULL,
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        -- Direct changes only: a row removed by ON DELETE CASCADE (a workspace or proposal being deleted)
        -- arrives at trigger depth > 1 and is let through.
        CREATE FUNCTION proposal.version_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' AND pg_trigger_depth() = 1 THEN
              RAISE EXCEPTION 'proposal version % is %, and cannot be deleted', OLD.version_id, OLD.status;
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.status = 'superseded'
             OR (OLD.status = 'approved' AND NOT (NEW.status = 'superseded'
                 AND to_jsonb(NEW) - 'status' - 'superseded_at' = to_jsonb(OLD) - 'status' - 'superseded_at')) THEN
            RAISE EXCEPTION 'proposal version % is %, and cannot be changed', OLD.version_id, OLD.status;
          END IF;
          RETURN NEW;
        END $$;

        CREATE TRIGGER version_frozen BEFORE UPDATE OR DELETE ON proposal.version
          FOR EACH ROW EXECUTE FUNCTION proposal.version_frozen();

        CREATE FUNCTION proposal.content_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          v_status text;
        BEGIN
          IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
            RETURN OLD;
          END IF;
          SELECT status INTO v_status FROM proposal.version
            WHERE version_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
          IF v_status <> 'draft' THEN
            RAISE EXCEPTION '% belongs to a % proposal version, and cannot be changed', TG_TABLE_NAME, v_status;
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.version_id <> NEW.version_id THEN
            RAISE EXCEPTION 'a % row cannot move between versions', TG_TABLE_NAME;
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$;

        CREATE TRIGGER line_frozen BEFORE INSERT OR UPDATE OR DELETE ON proposal.line
          FOR EACH ROW EXECUTE FUNCTION proposal.content_frozen();
        CREATE TRIGGER section_frozen BEFORE INSERT OR UPDATE OR DELETE ON proposal.section
          FOR EACH ROW EXECUTE FUNCTION proposal.content_frozen();

        CREATE FUNCTION proposal.append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION '% is append-only', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME;
        END $$;

        CREATE TRIGGER event_append_only BEFORE UPDATE OR DELETE ON proposal.event
          FOR EACH ROW EXECUTE FUNCTION proposal.append_only();
        CREATE TRIGGER output_append_only BEFORE UPDATE OR DELETE ON proposal.output
          FOR EACH ROW EXECUTE FUNCTION proposal.append_only();
        """
    )
    for table in TABLES:
        op.execute(
            f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY workspace ON {table}
              USING (org_id = tenancy.current_org()) WITH CHECK (org_id = tenancy.current_org());
            """
        )
    # Append-only lineage and confirmed records: still no direct change, but a workspace can now be deleted
    # (its rows arrive here through ON DELETE CASCADE, at trigger depth > 1).
    op.execute("""
        CREATE OR REPLACE FUNCTION lineage.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION '% is append-only', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME;
        END $$;
        """)
    op.execute("""
        GRANT USAGE ON SCHEMA proposal TO bidwright_app, bidwright_studio;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA proposal TO bidwright_app, bidwright_studio;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA proposal TO bidwright_app, bidwright_studio;
        """)


def downgrade() -> None:
    op.execute("DROP SCHEMA proposal CASCADE")
    op.execute("""
        CREATE OR REPLACE FUNCTION lineage.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME;
        END $$;
        """)
