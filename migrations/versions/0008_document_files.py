"""Keep original uploads and cached parser results for reindexing."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_document_files"
down_revision = "0007_retrieval_log_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("source_file_path", sa.Text()))
    op.add_column("documents", sa.Column("source_filename", sa.String(255)))
    op.add_column("documents", sa.Column("parser_metadata", JSONB()))
    op.add_column("documents", sa.Column("reparse_pending", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for name in ("reparse_pending", "parser_metadata", "source_filename", "source_file_path"):
        op.drop_column("documents", name)
