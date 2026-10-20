"""Allow only one active Agent turn per conversation across API workers.

Revision ID: 0005_active_turn_guard
Revises: 0004_conversation_plan
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_active_turn_guard"
down_revision = "0004_conversation_plan"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite and PostgreSQL both support partial unique indexes.
    # Existing multiple running turns must be reviewed before migration.
    op.create_index(
        "uq_running_turn_per_conversation", "turns", ["conversation_id"],
        unique=True, postgresql_where=sa.text("state = 'running'"),
        sqlite_where=sa.text("state = 'running'"),
    )


def downgrade():
    op.drop_index("uq_running_turn_per_conversation", table_name="turns")
