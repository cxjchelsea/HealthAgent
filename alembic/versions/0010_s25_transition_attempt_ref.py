"""Link task/step transitions to verified tool attempt evidence."""
from alembic import op
import sqlalchemy as sa
revision = "0010_s25_attempt_link"
down_revision = "0009_s25_transitions"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("agent_state_transitions",sa.Column("attempt_id",sa.String(40),nullable=True))
def downgrade():
    op.drop_column("agent_state_transitions","attempt_id")
