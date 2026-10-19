"""Initial S0/S1 tables matching services.db (no existing-data mutation).

Revision ID: 0001_s0_s1
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_s0_s1"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("conversations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_conversations_owner_id", "conversations", ["owner_id"])
    op.create_table("messages",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_table("turns",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("request_id", sa.String(120), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("assistant_message_id", sa.String(40), nullable=True),
        sa.Column("input_chars", sa.Integer(), nullable=False),
        sa.Column("output_chars", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.UniqueConstraint("conversation_id", "request_id", name="uq_turns_conversation_request"))
    op.create_index("ix_turns_conversation_id", "turns", ["conversation_id"])

def downgrade():
    op.drop_index("ix_turns_conversation_id", table_name="turns")
    op.drop_table("turns")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_owner_id", table_name="conversations")
    op.drop_table("conversations")
