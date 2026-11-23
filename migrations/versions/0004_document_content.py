"""Persist source text for asynchronous indexing."""

from alembic import op
import sqlalchemy as sa

revision = "0004_document_content"
down_revision = "0003_kb_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("content", sa.Text(), server_default="", nullable=False))


def downgrade() -> None:
    op.drop_column("documents", "content")
