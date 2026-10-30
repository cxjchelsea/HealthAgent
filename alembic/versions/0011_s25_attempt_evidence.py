"""S25-03: bounded durable tool attempt evidence.

Revision ID: 0007_s25_evidence
Revises: 0006_turn_diagnostics
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_s25_evidence"
down_revision = "0010_plan_recovery_audit"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "agent_attempts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("turn_id", sa.String(40), sa.ForeignKey("turns.id"), nullable=False),
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("step_id", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("arguments_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("result_projection", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("turn_id", "id", name="uq_agent_attempt_turn_id"),
    )
    op.create_index("ix_agent_attempts_conversation", "agent_attempts", ["conversation_id"])
    op.create_index("ix_agent_attempts_turn", "agent_attempts", ["turn_id"])

def downgrade():
    op.drop_index("ix_agent_attempts_turn", table_name="agent_attempts")
    op.drop_index("ix_agent_attempts_conversation", table_name="agent_attempts")
    op.drop_table("agent_attempts")
