"""S25-07 B3c-1 durable revision origin and request-bound resume approval.

Additive only. Legacy NULL request/dispatch version means no v2 execution authority.
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_b3c1_durable_revision_evidence"
down_revision = "0022_revision_approval_digest"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "primary_route_evidence",
        sa.Column("approved_request_digest", sa.String(64), nullable=True))
    op.add_column(
        "plan_tool_dispatches",
        sa.Column("plan_revision", sa.Integer(), nullable=True))
    op.create_table(
        "dependency_revision_anchors",
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id"),
                  primary_key=True),
        sa.Column("revision_turn_id", sa.String(40), sa.ForeignKey("turns.id"),
                  nullable=False, unique=True),
        sa.Column("approved_candidate_json", sa.Text(), nullable=False),
        sa.Column("approved_digest", sa.String(64), nullable=False),
        sa.Column("parent_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("origin_refs_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "dependency_v2_step_reservations",
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id"),
                  primary_key=True),
        sa.Column("step_id", sa.Integer(), primary_key=True),
        sa.Column("dispatch_id", sa.String(40), sa.ForeignKey("plan_tool_dispatches.id"),
                  nullable=False, unique=True),
        sa.Column("turn_id", sa.String(40), sa.ForeignKey("turns.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("dependency_v2_step_reservations")
    op.drop_table("dependency_revision_anchors")
    op.drop_column("plan_tool_dispatches", "plan_revision")
    op.drop_column("primary_route_evidence", "approved_request_digest")
