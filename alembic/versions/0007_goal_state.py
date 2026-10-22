"""Persist owner-associated minimal goal state for S25-01.

Revision ID: 0007_goal_state
Revises: 0006_turn_diagnostics
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_goal_state"
down_revision = "0006_turn_diagnostics"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "conversation_goal_states",
        sa.Column("conversation_id", sa.String(40),
                  sa.ForeignKey("conversations.id"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("goal_json", sa.Text(), nullable=True),
        sa.Column("pending_json", sa.Text(), nullable=True),
    )

def downgrade():
    op.drop_table("conversation_goal_states")
