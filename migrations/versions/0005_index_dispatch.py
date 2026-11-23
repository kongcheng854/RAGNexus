"""Keep a durable dispatch intent across the SQL commit and Redis publish."""

from alembic import op
import sqlalchemy as sa

revision = "0005_index_dispatch"
down_revision = "0004_document_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("dispatch_pending", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute("UPDATE documents SET dispatch_pending = true WHERE status = 3 AND content <> ''")
    # Older synchronous uploads have no retained source for a worker to resume.
    op.execute("UPDATE documents SET status = 2, error_message = '原文未保存，请重新上传' WHERE status = 3 AND content = ''")
    op.create_index("ix_documents_dispatch_pending", "documents", ["updated_at"], postgresql_where=sa.text("dispatch_pending"))


def downgrade() -> None:
    op.drop_index("ix_documents_dispatch_pending", table_name="documents")
    op.drop_column("documents", "dispatch_pending")
