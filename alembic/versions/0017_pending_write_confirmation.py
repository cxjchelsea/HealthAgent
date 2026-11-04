"""Durable candidate and explicit confirmation token for Primary Pending writes."""
from alembic import op
import sqlalchemy as sa

revision = "0017_pending_write_confirmation"
down_revision = "0016_primary_route_evidence"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite cannot ALTER a foreign-key constraint after ADD COLUMN.
    # Batch is cross-dialect and preserves existing data while rebuilding
    # the SQLite table with its new nullable FK column.
    with op.batch_alter_table("pending_write_authorities") as batch:
        batch.add_column(sa.Column(
            "candidate_turn_id", sa.String(40),
            sa.ForeignKey("turns.id", name="fk_pending_authority_candidate_turn"),
            nullable=True))
        batch.add_column(sa.Column(
            "candidate_title", sa.String(120), nullable=True))
        batch.add_column(sa.Column(
            "candidate_digest", sa.String(64), nullable=True))
        batch.add_column(sa.Column(
            "confirmation_nonce", sa.String(40), nullable=True))


def downgrade():
    with op.batch_alter_table("pending_write_authorities") as batch:
        batch.drop_column("confirmation_nonce")
        batch.drop_column("candidate_digest")
        batch.drop_column("candidate_title")
        batch.drop_column("candidate_turn_id")
