"""Enable pgvector and create the four core tables."""

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def timestamp(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_knowledge_base_scope"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("kb_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("error_message", sa.Text()),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "kb_id"], ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_documents_knowledge_base",
        ),
        sa.UniqueConstraint("tenant_id", "kb_id", "id", name="uq_document_scope"),
        sa.CheckConstraint("status IN (1, 2, 3)", name="ck_document_status"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("kb_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "kb_id", "document_id"],
            ["documents.tenant_id", "documents.kb_id", "documents.id"],
            name="fk_chunks_document",
        ),
    )
    op.create_index("ix_chunks_scope", "chunks", ["tenant_id", "kb_id", "document_id"])
    op.create_table(
        "retrieval_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("kb_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("retrieved_chunks", JSONB(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("vector_store", sa.String(32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "kb_id"], ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_retrieval_logs_knowledge_base",
        ),
    )
    op.create_index("ix_retrieval_logs_scope", "retrieval_logs", ["tenant_id", "kb_id"])


def downgrade() -> None:
    op.drop_table("retrieval_logs")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("knowledge_bases")
    # Leave the vector extension available for other tables in the database.
