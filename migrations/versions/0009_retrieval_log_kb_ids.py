"""Record the ordered knowledge bases searched by each retrieval."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_retrieval_log_kb_ids"
down_revision = "0008_document_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("retrieval_logs", sa.Column("kb_ids", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("retrieval_logs", "kb_ids")
