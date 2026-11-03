"""Durable, one-use Pending write authority after explicit v1 clarification."""
from alembic import op
import sqlalchemy as sa

revision = "0015_primary_pending_write"
down_revision = "0014_s25_attempt_link"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pending_write_authorities",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40),
                  sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("origin_turn_id", sa.String(40),
                  sa.ForeignKey("turns.id"), nullable=False, unique=True),
        sa.Column("origin_user_message_id", sa.String(40),
                  sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False,
                  server_default="pending"),
        sa.Column("consumed_turn_id", sa.String(40),
                  sa.ForeignKey("turns.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pending_write_authorities_conversation_id",
                    "pending_write_authorities", ["conversation_id"])


def downgrade():
    op.drop_index("ix_pending_write_authorities_conversation_id",
                  table_name="pending_write_authorities")
    op.drop_table("pending_write_authorities")
