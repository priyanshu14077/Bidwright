"""Gate 1, "What we have": evidence for every conclusion, the assessment, follow-up questions, decisions.

- lineage.evidence: why the system believes something. Every conclusion it shows (a field's value or status,
  an estimate, a rule that fired, a fee factor, a chat answer) points to one or more rows here: the quoted
  text in a message or document (kind text), the client's reply (reply), past projects (comparable), a rule
  and the values it tested (rule), a workspace setting (setting), or a value the engine worked out (engine).
  Each row has a one-line "why".
- intake.assessment: the latest assessment of a request (status per field, estimates, completeness, rules,
  recommendation), written by the engine after every run.
- intake.followup: the questions to ask the client, worded by the model, and one message ready to paste.
- intake.gate_decision: a person's decision at gate 1 (qualify, ask the client, decline with a reason).
  Append-only; the latest row is the current decision.
- ref.intake_setting: per-workspace intake policy (stage defaults by kind of ask, decline reasons, question
  order, ...), seeded from the starter pack.
- ref.studio.markets: the countries a studio covers, so a request is routed to the studio that serves its
  market rather than to whichever wrote the most past proposals there.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-20
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

ORG = "org_id uuid NOT NULL DEFAULT tenancy.current_org() REFERENCES tenancy.organization ON DELETE CASCADE"
TABLES = ["lineage.evidence", "intake.assessment", "intake.followup", "intake.gate_decision", "ref.intake_setting"]


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE lineage.evidence (
          evidence_id bigserial PRIMARY KEY,
          {ORG},
          envelope_id bigint NOT NULL REFERENCES intake.envelope ON DELETE CASCADE,
          subject text NOT NULL,
          kind text NOT NULL CHECK (kind IN ('text', 'reply', 'comparable', 'rule', 'setting', 'engine')),
          document_id bigint REFERENCES lineage.source_document ON DELETE CASCADE,
          field_value_id bigint REFERENCES lineage.field_value ON DELETE CASCADE,
          page integer,
          rects jsonb,
          span integer[],
          quote text,
          ref jsonb,
          why text NOT NULL CHECK (length(why) > 0),
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX evidence_by_envelope ON lineage.evidence (envelope_id, subject);

        CREATE TABLE intake.assessment (
          {ORG},
          envelope_id bigint PRIMARY KEY REFERENCES intake.envelope ON DELETE CASCADE,
          assessment jsonb NOT NULL,
          computed_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE intake.followup (
          {ORG},
          envelope_id bigint PRIMARY KEY REFERENCES intake.envelope ON DELETE CASCADE,
          questions jsonb NOT NULL,
          message text NOT NULL,
          fields text[] NOT NULL,
          model text,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE intake.gate_decision (
          decision_id bigserial PRIMARY KEY,
          {ORG},
          envelope_id bigint NOT NULL REFERENCES intake.envelope ON DELETE CASCADE,
          gate smallint NOT NULL DEFAULT 1,
          decision text NOT NULL CHECK (decision IN ('qualify', 'ask', 'decline')),
          recommendation text,
          reason text,
          note text,
          decided_by text NOT NULL,
          decided_at timestamptz NOT NULL DEFAULT now(),
          CHECK (decision <> 'decline' OR reason IS NOT NULL)
        );
        CREATE INDEX gate_decision_by_envelope ON intake.gate_decision (envelope_id, decided_at DESC);
        CREATE TRIGGER gate_decision_append_only BEFORE UPDATE OR DELETE ON intake.gate_decision
          FOR EACH ROW EXECUTE FUNCTION lineage.forbid_mutation();

        CREATE TABLE ref.intake_setting (
          {ORG},
          key text NOT NULL,
          value jsonb NOT NULL,
          updated_by text,
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (org_id, key)
        );

        ALTER TABLE ref.studio ADD COLUMN markets char(2)[] NOT NULL DEFAULT '{{}}';
    """)
    for table in TABLES:
        op.execute(f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY workspace ON {table}
              USING (org_id = tenancy.current_org()) WITH CHECK (org_id = tenancy.current_org());
            GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO bidwright_app, bidwright_studio;
        """)
    op.execute("""
        GRANT USAGE, SELECT ON SEQUENCE lineage.evidence_evidence_id_seq, intake.gate_decision_decision_id_seq
          TO bidwright_app, bidwright_studio;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE ref.studio DROP COLUMN markets;
        DROP TABLE ref.intake_setting, intake.gate_decision, intake.followup, intake.assessment, lineage.evidence;
    """)
