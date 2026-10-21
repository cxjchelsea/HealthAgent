"""Persist bounded, non-sensitive Agent turn failure diagnostics.

Revision ID: 0006_turn_diagnostics
Revises: 0005_active_turn_guard
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_turn_diagnostics"
down_revision = "0005_active_turn_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("turns", sa.Column("failure_code", sa.String(64), nullable=True))
    op.add_column("turns", sa.Column("decision_steps", sa.Integer(), nullable=False,
                                    server_default="0"))


def downgrade():
    op.drop_column("turns", "decision_steps")
    op.drop_column("turns", "failure_code")
