from datetime import datetime
from enum import IntEnum
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, Timestamps
from app.models.tenant import Tenant
from app.models.api_key import ApiKey


class IndexStatus(IntEnum):
    SUCCESS = 1
    FAILED = 2
    PROCESSING = 3


class KnowledgeBase(Timestamps, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_knowledge_base_scope"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)


class Document(Timestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "kb_id"], ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_documents_knowledge_base",
        ),
        UniqueConstraint("tenant_id", "kb_id", "id", name="uq_document_scope"),
        CheckConstraint("status IN (1, 2, 3)", name="ck_document_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    kb_id: Mapped[UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[int] = mapped_column(SmallInteger)
    error_message: Mapped[str | None] = mapped_column(Text)


class Chunk(CreatedAt, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "kb_id", "document_id"],
            ["documents.tenant_id", "documents.kb_id", "documents.id"],
            name="fk_chunks_document",
        ),
        Index("ix_chunks_scope", "tenant_id", "kb_id", "document_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    kb_id: Mapped[UUID] = mapped_column(Uuid)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSONB)
    embedding: Mapped[list[float]] = mapped_column(Vector())


class RetrievalLog(CreatedAt, Base):
    __tablename__ = "retrieval_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "kb_id"], ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_retrieval_logs_knowledge_base",
        ),
        Index("ix_retrieval_logs_scope", "tenant_id", "kb_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    kb_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[str] = mapped_column(String(128))
    query: Mapped[str] = mapped_column(Text)
    retrieved_chunks: Mapped[list[dict]] = mapped_column(JSONB)
    top_k: Mapped[int]
    vector_store: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int]
