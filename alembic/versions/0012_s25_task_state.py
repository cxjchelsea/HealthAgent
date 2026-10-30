"""S25-03: durable versioned Agent task state, separate from user todo items."""
from alembic import op
import sqlalchemy as sa
revision="0012_s25_task_state"
down_revision = "0011_s25_evidence"
branch_labels=None
depends_on=None
def upgrade():
    op.create_table("agent_task_states",
        sa.Column("conversation_id",sa.String(40),sa.ForeignKey("conversations.id"),primary_key=True),
        sa.Column("task_id",sa.String(40),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False,server_default="1"),
        sa.Column("plan_revision",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("state",sa.String(24),nullable=False),
        sa.Column("step_states",sa.Text(),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False))
def downgrade():
    op.drop_table("agent_task_states")
