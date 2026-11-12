"""B2b3: immutable fingerprint of the Host-approved revision candidate.

Existing admissions (including B2b2 proposal-only historical Turns) remain
NULL and are never retroactively authorized for CAS.
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_revision_approval_digest"
down_revision = "0021_turn_user_message"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "primary_route_evidence",
        sa.Column("approved_revision_digest", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("primary_route_evidence", "approved_revision_digest")
