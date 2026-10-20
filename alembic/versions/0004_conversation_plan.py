"""Persist owner-scoped conversation planning checkpoint.

Revision ID: 0004_conversation_plan
Revises: 0003_memory
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_conversation_plan"
down_revision = "0003_memory"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("conversation_plans",
                    sa.Column("conversation_id", sa.String(40),
                              sa.ForeignKey("conversations.id"), primary_key=True),
                    sa.Column("snapshot", sa.Text(), nullable=False))

def downgrade():
    op.drop_table("conversation_plans")
