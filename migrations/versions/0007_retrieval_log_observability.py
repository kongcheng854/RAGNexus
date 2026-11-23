"""Correlate retrieval logs with Langfuse traces and the effective queries."""

from alembic import op
import sqlalchemy as sa

revision = "0007_retrieval_log_observability"
down_revision = "0006_tenant_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("retrieval_logs", sa.Column("trace_id", sa.String(64), nullable=True))
    op.add_column("retrieval_logs", sa.Column("profile", sa.String(32), nullable=True))
    op.add_column("retrieval_logs", sa.Column("effective_query", sa.Text(), nullable=True))
    op.add_column("retrieval_logs", sa.Column("search_query", sa.Text(), nullable=True))
    op.create_index("ix_retrieval_logs_tenant_trace", "retrieval_logs", ["tenant_id", "trace_id"],
                    postgresql_where=sa.text("trace_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_retrieval_logs_tenant_trace", table_name="retrieval_logs")
    for column in ("search_query", "effective_query", "profile", "trace_id"):
        op.drop_column("retrieval_logs", column)
