"""Durable expiration of a staged Pending task candidate."""
from alembic import op
import sqlalchemy as sa

revision = "0018_pending_expiry"
down_revision = "0017_pending_write_confirmation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pending_write_authorities", sa.Column(
        "candidate_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("pending_write_authorities", "candidate_expires_at")
