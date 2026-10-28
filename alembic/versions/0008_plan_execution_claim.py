"""Durable claim for saved-plan tool execution.

Revision ID: 0008_plan_execution_claim
Revises: 0007_goal_state
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_plan_execution_claim"
down_revision = "0007_goal_state"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "plan_execution_claims",
        sa.Column("conversation_id", sa.String(40),
                  sa.ForeignKey("conversations.id"), primary_key=True),
        sa.Column("turn_id", sa.String(40), sa.ForeignKey("turns.id"),
                  nullable=False, unique=True),
        sa.Column("snapshot", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

def downgrade():
    op.drop_table("plan_execution_claims")
