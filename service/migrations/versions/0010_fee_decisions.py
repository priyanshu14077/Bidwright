"""Gate 2 on the fee card: approve, override (with an amount) or reject, each with a reason where it matters.

The same append-only intake.gate_decision table holds both gates. A gate 2 decision records the amount the
director approved, its currency, the engine's figure at the time, and the proposal it created.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-20
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE intake.gate_decision DROP CONSTRAINT gate_decision_decision_check;
        ALTER TABLE intake.gate_decision ADD CONSTRAINT gate_decision_decision_check
          CHECK ((gate = 1 AND decision IN ('qualify', 'ask', 'decline'))
                 OR (gate = 2 AND decision IN ('approve', 'override', 'reject')));
        ALTER TABLE intake.gate_decision
          ADD COLUMN amount numeric(16, 2), ADD COLUMN engine_amount numeric(16, 2), ADD COLUMN currency text,
          ADD COLUMN proposal_id bigint REFERENCES proposal.bid ON DELETE CASCADE,
          ADD CONSTRAINT gate_decision_reason_check CHECK (decision NOT IN ('override', 'reject') OR reason IS NOT NULL),
          ADD CONSTRAINT gate_decision_amount_check CHECK (decision <> 'override' OR amount > 0);
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE intake.gate_decision DISABLE TRIGGER gate_decision_append_only;
        DELETE FROM intake.gate_decision WHERE gate = 2;
        ALTER TABLE intake.gate_decision ENABLE TRIGGER gate_decision_append_only;
        ALTER TABLE intake.gate_decision DROP CONSTRAINT gate_decision_amount_check,
          DROP CONSTRAINT gate_decision_reason_check, DROP COLUMN proposal_id, DROP COLUMN currency,
          DROP COLUMN engine_amount, DROP COLUMN amount;
        ALTER TABLE intake.gate_decision DROP CONSTRAINT gate_decision_decision_check;
        ALTER TABLE intake.gate_decision ADD CONSTRAINT gate_decision_decision_check
          CHECK (decision IN ('qualify', 'ask', 'decline'));
    """)
