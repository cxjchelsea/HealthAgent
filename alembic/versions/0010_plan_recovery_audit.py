"""Audited operator recovery.
Revision ID: 0010_plan_recovery_audit
Revises: 0009_plan_tool_dispatch
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_plan_recovery_audit"
down_revision = "0009_plan_tool_dispatch"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("plan_recovery_audits",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), nullable=False),
        sa.Column("turn_id", sa.String(40), nullable=False),
        sa.Column("operator_id", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(48), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_plan_recovery_audits_conversation_id",
                    "plan_recovery_audits", ["conversation_id"])

def downgrade():
    op.drop_index("ix_plan_recovery_audits_conversation_id", table_name="plan_recovery_audits")
    op.drop_table("plan_recovery_audits")
