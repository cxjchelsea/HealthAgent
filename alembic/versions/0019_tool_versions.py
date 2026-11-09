"""S25-05: nullable tool version provenance for attempts and plan dispatch.

NULL means unknown / historical; never backfill with the current registry version.
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_tool_versions"
down_revision = "0018_pending_expiry"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_attempts", sa.Column("tool_version", sa.String(32), nullable=True))
    op.add_column("plan_tool_dispatches", sa.Column("tool_version", sa.String(32), nullable=True))


def downgrade():
    op.drop_column("plan_tool_dispatches", "tool_version")
    op.drop_column("agent_attempts", "tool_version")
