"""Store retrieval diagnostics, including partial and empty results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_retrieval_metadata"
down_revision = "0009_retrieval_log_kb_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("retrieval_logs", sa.Column("retrieval_metadata", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("retrieval_logs", "retrieval_metadata")
