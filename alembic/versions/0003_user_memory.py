"""Persistent user memory migration."""
from alembic import op
import sqlalchemy as sa
revision = "0003_memory"
down_revision = "0002_tasks"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("user_memories",
        sa.Column("id",sa.String(40),primary_key=True),
        sa.Column("owner_id",sa.String(128),nullable=False),
        sa.Column("key",sa.String(80),nullable=False),
        sa.Column("value",sa.String(500),nullable=False),
        sa.UniqueConstraint("owner_id","key",name="uq_user_memory_owner_key"))
    op.create_index("ix_user_memories_owner_id","user_memories",["owner_id"])
def downgrade():
    raise RuntimeError("Destructive downgrade requires review")
