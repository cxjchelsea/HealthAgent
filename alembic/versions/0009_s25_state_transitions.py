"""S25-03: append-only, bounded task/step transition history."""
from alembic import op
import sqlalchemy as sa
revision = "0009_s25_transitions"
down_revision = "0008_s25_task_state"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("agent_state_transitions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("task_id", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("step_id", sa.Integer(), nullable=True),
        sa.Column("from_state", sa.String(24), nullable=True),
        sa.Column("to_state", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_transitions_conversation", "agent_state_transitions", ["conversation_id"])

def downgrade():
    op.drop_index("ix_agent_transitions_conversation", table_name="agent_state_transitions")
    op.drop_table("agent_state_transitions")
