"""Immutable, pre-dispatch Primary route and Host admission audit record."""
from alembic import op
import sqlalchemy as sa

revision = "0016_primary_route_evidence"
down_revision = "0015_primary_pending_write"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "primary_route_evidence",
        sa.Column("turn_id", sa.String(40),
                  sa.ForeignKey("turns.id"), primary_key=True),
        sa.Column("conversation_id", sa.String(40),
                  sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("proposal_id", sa.String(40), nullable=False, unique=True),
        sa.Column("schema_version", sa.String(40), nullable=True),
        sa.Column("route_kind", sa.String(40), nullable=True),
        sa.Column("semantic_attempts", sa.Integer(), nullable=False),
        sa.Column("preturn_snapshot_digest", sa.String(64), nullable=False),
        sa.Column("disposition", sa.String(20), nullable=False),
        sa.Column("admission_reason", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_primary_route_evidence_conversation_id",
                    "primary_route_evidence", ["conversation_id"])


def downgrade():
    op.drop_index("ix_primary_route_evidence_conversation_id",
                  table_name="primary_route_evidence")
    op.drop_table("primary_route_evidence")
