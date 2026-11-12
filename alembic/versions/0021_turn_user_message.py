"""S25-07 B2b1: bind each new Turn to its exact current user Message.

Historical Turns cannot be safely backfilled from chronology or roles;
their NULL association deliberately fails closed for revision admission.
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_turn_user_message"
down_revision = "0020_dependency_fence"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("turns") as batch:
        batch.add_column(sa.Column(
            "user_message_id", sa.String(40), nullable=True))
        batch.create_foreign_key(
            "fk_turn_user_message", "messages",
            ["user_message_id"], ["id"])


def downgrade():
    with op.batch_alter_table("turns") as batch:
        batch.drop_constraint("fk_turn_user_message", type_="foreignkey")
        batch.drop_column("user_message_id")
