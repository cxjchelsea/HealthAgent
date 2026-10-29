"""M4 durable plan tool dispatch intention and observed receipt.
Revision ID: 0009_plan_tool_dispatch
Revises: 0008_plan_execution_claim
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_plan_tool_dispatch"
down_revision = "0008_plan_execution_claim"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "plan_tool_dispatches",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("turn_id", sa.String(40), sa.ForeignKey("turns.id"), nullable=False),
        sa.Column("step_id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("receipt_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("turn_id", "step_id", name="uq_plan_dispatch_turn_step"),
    )
    op.create_index("ix_plan_tool_dispatches_conversation_id", "plan_tool_dispatches", ["conversation_id"])
    op.create_index("ix_plan_tool_dispatches_turn_id", "plan_tool_dispatches", ["turn_id"])

def downgrade():
    op.drop_index("ix_plan_tool_dispatches_turn_id", table_name="plan_tool_dispatches")
    op.drop_index("ix_plan_tool_dispatches_conversation_id", table_name="plan_tool_dispatches")
    op.drop_table("plan_tool_dispatches")
