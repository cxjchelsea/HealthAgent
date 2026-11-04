"""Durable candidate and explicit confirmation token for Primary Pending writes."""
from alembic import op
import sqlalchemy as sa

revision = "0017_pending_write_confirmation"
down_revision = "0016_primary_route_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pending_write_authorities",
                  sa.Column("candidate_turn_id", sa.String(40),
                            sa.ForeignKey("turns.id"), nullable=True))
    op.add_column("pending_write_authorities",
                  sa.Column("candidate_title", sa.String(120), nullable=True))
    op.add_column("pending_write_authorities",
                  sa.Column("candidate_digest", sa.String(64), nullable=True))
    op.add_column("pending_write_authorities",
                  sa.Column("confirmation_nonce", sa.String(40), nullable=True))


def downgrade():
    op.drop_column("pending_write_authorities", "confirmation_nonce")
    op.drop_column("pending_write_authorities", "candidate_digest")
    op.drop_column("pending_write_authorities", "candidate_title")
    op.drop_column("pending_write_authorities", "candidate_turn_id")
