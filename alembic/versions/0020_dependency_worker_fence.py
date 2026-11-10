"""S25-06 new-format worker generation fence; legacy Claim unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "0020_dependency_fence"
down_revision = "0019_tool_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dependency_worker_fences",
        sa.Column("conversation_id", sa.String(40),
                  sa.ForeignKey("conversations.id"), primary_key=True),
        sa.Column("turn_id", sa.String(40),
                  sa.ForeignKey("turns.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("dependency_worker_fences")
