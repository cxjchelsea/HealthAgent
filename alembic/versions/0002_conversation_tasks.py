"""Create persistent conversation task records.

Revision ID: 0002_tasks
Revises: 0001_s0_s1
"""
from alembic import op
import sqlalchemy as sa
revision = "0002_tasks"
down_revision = "0001_s0_s1"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("conversation_tasks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_conversation_tasks_conversation_id", "conversation_tasks", ["conversation_id"])
def downgrade():
    raise RuntimeError("Data-preserving migration: manual review required")
